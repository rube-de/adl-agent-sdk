# Telegram Interactive Escalation — Design Amendment

> Replaces the notification-only Telegram integration (v3 §7.8) with an interactive layered client adopting patterns from [takopi](https://github.com/banteg/takopi). Adds live progress updates, inline keyboard escalation, and callback-driven human-in-the-loop.

**Date:** 2026-02-27
**Status:** Draft
**Amends:** `2026-02-27-auto-dev-loop-v3-python-design.md` §2, §7.8, §7.9, §12, §13, §14
**Amends:** `2026-02-27-workflow-abstraction-design.md` §5 (`escalate_to_human`)
**Inspired by:** [banteg/takopi](https://github.com/banteg/takopi) Telegram bridge architecture

---

## 0. Changes from v3 Telegram Design

| Aspect | v3 (before) | This amendment |
|---|---|---|
| Direction | Write-only (notifications out) | **Bidirectional** (notifications out, decisions in) |
| Escalation | File-based (`needs_human.json`) | **Inline keyboard callbacks** (approve/reject/reply) |
| Progress | None | **Live-updating progress cards** (one message per issue, edited in-place) |
| Client arch | Single `telegram.py` with raw httpx | **Layered package**: `HttpBotClient` → `TelegramClient` → `TelegramOutbox` → `TelegramPoller` |
| Rate limiting | None | **Per-chat token bucket** (1 rps private, 20/min group) |
| Message queuing | None | **Priority outbox** with edit coalescing |
| API models | Raw dicts | **msgspec structs** |
| Error handling | Unspecified | **RetryAfter backoff**, graceful degradation on network errors |

### Patterns adopted from takopi

| Pattern | takopi source | How adopted |
|---|---|---|
| Layered client architecture | `telegram/client_api.py` → `client.py` → `outbox.py` | Same 4-layer stack, scoped to ADL's needs |
| Priority outbox with edit coalescing | `telegram/outbox.py` | SEND > DELETE > EDIT priority; rapid edits to same message_id collapse |
| Per-chat rate limiter | `telegram/client.py` | Token bucket, 1 rps private / 20 per 60s group |
| RetryAfter as typed exception | `telegram/client_api.py` | Parsed from 429 response, re-enqueued with backoff |
| msgspec for API models | Throughout takopi | Minimal typed structs for ADL's Telegram API subset |
| Long-polling with dedup | `telegram/loop.py` | Offset-based polling, seen-update-ID dedup |
| Inline keyboard callbacks | Cancel button pattern | Escalation buttons: approve/reject/reply |

### Patterns NOT adopted

| Pattern | Reason |
|---|---|
| Plugin system (entrypoints) | Over-engineered for single-purpose Telegram |
| anyio structured concurrency | ADL is asyncio; anyio adds indirection with no benefit |
| Voice transcription (Whisper) | ADL doesn't need voice input |
| Forum topics / thread scheduler | ADL uses single chat_id, not group forums |
| Forward coalescing | ADL doesn't receive user-forwarded messages |
| Subprocess runner (JSONL bridge) | ADL uses SDK `query()` natively |
| Config hot-reload (watchfiles) | Deferred — may adopt later for agent hot-reload (open question #5) |

---

## 1. Module Architecture

The single `telegram.py` module becomes a `telegram/` package with layered responsibilities:

```
src/auto_dev_loop/telegram/
    __init__.py          # Public API: TelegramBot facade
    bot_api.py           # Layer 1: Raw HTTP client (httpx → Bot API)
    client.py            # Layer 2: Rate-limited client wrapper
    outbox.py            # Layer 3: Priority queue + edit coalescing
    poller.py            # Layer 4: Long-polling + callback routing
    models.py            # msgspec structs for Telegram API types
    messages.py          # Message builders (progress, escalation)
    callbacks.py         # Callback data encoding/decoding
```

Dependency flow: `TelegramBot` → `TelegramOutbox` → `TelegramClient` → `HttpBotClient`. Each layer only depends downward. The rest of ADL only touches `TelegramBot`.

---

## 2. Layer 1 — `bot_api.py` (Raw HTTP Client)

Direct httpx calls to the Telegram Bot API. No rate limiting, no queuing.

```python
import httpx
import msgspec
from .models import BotApiResponse, Message, Update, RetryAfter, BotApiError

class HttpBotClient:
    """Raw Telegram Bot API over httpx. No rate limiting, no retry."""

    def __init__(self, token: str):
        self._base = f"https://api.telegram.org/bot{token}"
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    async def call(self, method: str, **params) -> BotApiResponse:
        resp = await self._http.post(f"{self._base}/{method}", json=params)
        parsed = msgspec.json.decode(resp.content, type=BotApiResponse)
        if not parsed.ok:
            if parsed.error_code == 429:
                raise RetryAfter(parsed.parameters.retry_after)
            raise BotApiError(parsed.error_code, parsed.description)
        return parsed

    async def send_message(
        self, chat_id: int, text: str,
        reply_markup: dict | None = None, parse_mode: str = "HTML",
    ) -> Message:
        resp = await self.call(
            "sendMessage",
            chat_id=chat_id, text=text,
            reply_markup=reply_markup, parse_mode=parse_mode,
        )
        return msgspec.json.decode(msgspec.json.encode(resp.result), type=Message)

    async def edit_message_text(
        self, chat_id: int, message_id: int, text: str,
        reply_markup: dict | None = None, parse_mode: str = "HTML",
    ) -> Message:
        resp = await self.call(
            "editMessageText",
            chat_id=chat_id, message_id=message_id, text=text,
            reply_markup=reply_markup, parse_mode=parse_mode,
        )
        return msgspec.json.decode(msgspec.json.encode(resp.result), type=Message)

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        await self.call("deleteMessage", chat_id=chat_id, message_id=message_id)
        return True

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None,
    ) -> bool:
        await self.call("answerCallbackQuery", callback_query_id=callback_query_id, text=text)
        return True

    async def get_updates(
        self, offset: int | None = None, timeout: int = 50,
    ) -> list[Update]:
        params = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        resp = await self.call("getUpdates", **params)
        return msgspec.json.decode(msgspec.json.encode(resp.result), type=list[Update])

    async def close(self) -> None:
        await self._http.aclose()
```

Design notes:
- **60s read timeout, 10s connect** — matches Telegram's long-poll window
- **`allowed_updates` filter** — only receive messages and callback queries, ignore everything else
- **`RetryAfter` as typed exception** — propagates to outbox for backoff scheduling

---

## 3. Layer 2 — `client.py` (Rate-Limited Client)

Wraps `HttpBotClient` with per-chat token-bucket rate limiting.

```python
import asyncio
import time

class RateLimiter:
    """Token-bucket rate limiter per chat_id."""

    def __init__(self, rate: float, burst: int = 1):
        self._rate = rate
        self._burst = burst
        self._buckets: dict[int, float] = {}   # chat_id → available tokens
        self._last_refill: dict[int, float] = {}

    async def acquire(self, chat_id: int) -> None:
        now = time.monotonic()
        if chat_id not in self._buckets:
            self._buckets[chat_id] = self._burst
            self._last_refill[chat_id] = now

        # Refill
        elapsed = now - self._last_refill[chat_id]
        self._buckets[chat_id] = min(
            self._burst,
            self._buckets[chat_id] + elapsed * self._rate,
        )
        self._last_refill[chat_id] = now

        # Wait if empty
        if self._buckets[chat_id] < 1.0:
            wait = (1.0 - self._buckets[chat_id]) / self._rate
            await asyncio.sleep(wait)
            self._buckets[chat_id] = 0.0
        else:
            self._buckets[chat_id] -= 1.0


class TelegramClient:
    """Rate-limited Telegram client. Same interface as HttpBotClient."""

    def __init__(self, bot_api: HttpBotClient, chat_type: str = "private"):
        self._api = bot_api
        self._limiter = RateLimiter(
            rate=1.0 if chat_type == "private" else 20.0 / 60.0,
        )

    async def send_message(self, chat_id: int, text: str, **kw) -> Message:
        await self._limiter.acquire(chat_id)
        return await self._api.send_message(chat_id, text, **kw)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, **kw) -> Message:
        await self._limiter.acquire(chat_id)
        return await self._api.edit_message_text(chat_id, message_id, text, **kw)

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        await self._limiter.acquire(chat_id)
        return await self._api.delete_message(chat_id, message_id)

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> bool:
        # Callback answers are not rate-limited (different endpoint)
        return await self._api.answer_callback_query(callback_query_id, text)
```

Rates from Telegram's documented limits:
- **Private chat:** 1 message per second
- **Group chat:** 20 messages per minute

---

## 4. Layer 3 — `outbox.py` (Priority Queue + Edit Coalescing)

The outbox decouples "intent to send" from "actually hitting the API". Provides priority ordering and edit coalescing to prevent rate limit exhaustion during busy dev cycles.

```python
import asyncio
import time
from enum import IntEnum
from dataclasses import dataclass, field

class Priority(IntEnum):
    SEND = 0      # New messages have highest priority
    DELETE = 1
    EDIT = 2      # Progress edits are lowest (and coalesced)

@dataclass(order=True)
class OutboxItem:
    priority: Priority
    sequence: int = field(compare=True)   # FIFO within same priority
    method: str = field(compare=False)
    kwargs: dict = field(compare=False)
    message_key: str | None = field(compare=False, default=None)
    retry_at: float = field(compare=False, default=0.0)
    future: asyncio.Future | None = field(compare=False, default=None)


class TelegramOutbox:
    """Async priority queue with edit coalescing and RetryAfter backoff."""

    def __init__(self, client: TelegramClient):
        self._client = client
        self._queue: asyncio.PriorityQueue[OutboxItem] = asyncio.PriorityQueue()
        self._pending_edits: dict[str, OutboxItem] = {}
        self._seq = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def enqueue_send(self, chat_id: int, text: str, **kw) -> asyncio.Future:
        """Enqueue a new message. Returns Future that resolves to Message."""
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._queue.put(OutboxItem(
            priority=Priority.SEND, sequence=self._next_seq(),
            method="send_message",
            kwargs={"chat_id": chat_id, "text": text, **kw},
            future=future,
        ))
        return future

    async def enqueue_edit(self, chat_id: int, message_id: int, text: str, **kw) -> None:
        """Enqueue an edit. Coalesces with any pending edit for same message."""
        key = f"{chat_id}:{message_id}"
        item = OutboxItem(
            priority=Priority.EDIT, sequence=self._next_seq(),
            method="edit_message_text",
            kwargs={"chat_id": chat_id, "message_id": message_id, "text": text, **kw},
            message_key=key,
        )
        if key in self._pending_edits:
            # Replace pending edit — only latest content matters
            self._pending_edits[key].kwargs = item.kwargs
        else:
            self._pending_edits[key] = item
            await self._queue.put(item)

    async def enqueue_delete(self, chat_id: int, message_id: int) -> None:
        await self._queue.put(OutboxItem(
            priority=Priority.DELETE, sequence=self._next_seq(),
            method="delete_message",
            kwargs={"chat_id": chat_id, "message_id": message_id},
        ))

    async def drain_loop(self) -> None:
        """Background task: process outbox queue forever."""
        while True:
            item = await self._queue.get()

            # For edits, always use the latest coalesced version
            if item.message_key:
                if item.message_key in self._pending_edits:
                    item = self._pending_edits.pop(item.message_key)
                else:
                    continue  # Already sent by a previous drain cycle

            # Honor RetryAfter backoff
            now = time.monotonic()
            if item.retry_at > now:
                await asyncio.sleep(item.retry_at - now)

            try:
                result = await getattr(self._client, item.method)(**item.kwargs)
                if item.future and not item.future.done():
                    item.future.set_result(result)
            except RetryAfter as e:
                item.retry_at = time.monotonic() + e.retry_after
                await self._queue.put(item)
            except BotApiError:
                if item.future and not item.future.done():
                    item.future.set_result(None)
                # Log and continue — don't crash the outbox
```

### Why coalescing matters

A dev cycle running for 2 minutes may trigger 20+ stage-update events. Without coalescing, each becomes an API call — burning through rate limits on progress noise. The outbox collapses rapid edits to the same `message_id` into a single call containing the latest content. takopi discovered this necessity empirically.

---

## 5. Layer 4 — `poller.py` (Long-Polling + Callback Routing)

Receives incoming Telegram updates. Routes callback queries (button presses) and text replies to registered handlers.

```python
import asyncio
import logging
import httpx

log = logging.getLogger(__name__)

CallbackHandler = Callable[[CallbackQuery], Awaitable[None]]
ReplyHandler = Callable[[Message], Awaitable[None]]


class TelegramPoller:
    """Long-polling loop. Routes callbacks and replies to handlers."""

    def __init__(self, bot_api: HttpBotClient):
        self._api = bot_api
        self._offset: int = 0
        self._seen: set[int] = set()
        self._callback_handlers: dict[str, CallbackHandler] = {}
        self._reply_handlers: dict[int, ReplyHandler] = {}

    def on_callback(self, prefix: str, handler: CallbackHandler) -> None:
        """Register handler for callback_data starting with prefix."""
        self._callback_handlers[prefix] = handler

    def on_reply_to(self, message_id: int, handler: ReplyHandler) -> None:
        """Register one-shot handler for replies to a specific message."""
        self._reply_handlers[message_id] = handler

    async def poll_loop(self) -> None:
        """Long-poll Telegram for updates. Runs forever."""
        while True:
            try:
                updates = await self._api.get_updates(
                    offset=self._offset, timeout=50,
                )
            except httpx.HTTPError:
                log.warning("Telegram poll failed, retrying in 5s")
                await asyncio.sleep(5)
                continue

            for update in updates:
                self._offset = update.update_id + 1

                if update.update_id in self._seen:
                    continue
                self._seen.add(update.update_id)
                # Bound dedup set size
                if len(self._seen) > 10_000:
                    self._seen = set(sorted(self._seen)[-5_000:])

                if update.callback_query:
                    await self._route_callback(update.callback_query)
                elif update.message and update.message.reply_to_message:
                    await self._route_reply(update.message)
                # All other update types are ignored

    async def _route_callback(self, cb: CallbackQuery) -> None:
        if not cb.data:
            return
        for prefix, handler in self._callback_handlers.items():
            if cb.data.startswith(prefix):
                try:
                    await handler(cb)
                except Exception:
                    log.exception(f"Callback handler error: {cb.data}")
                return

    async def _route_reply(self, msg: Message) -> None:
        reply_to_id = msg.reply_to_message.message_id
        handler = self._reply_handlers.pop(reply_to_id, None)
        if handler:
            try:
                await handler(msg)
            except Exception:
                log.exception(f"Reply handler error: reply to {reply_to_id}")
```

ADL only needs two routing paths:
- **Callback queries** — inline button presses (approve/reject/provide-feedback)
- **Reply-to messages** — human types a text reply to an escalation message

Everything else is dropped. No command parsing, no voice, no file uploads.

---

## 6. `models.py` (msgspec Telegram Types)

Minimal typed structs for the Telegram API subset ADL actually uses. ~60 LOC.

```python
import msgspec


class Chat(msgspec.Struct):
    id: int
    type: str  # "private", "group", "supergroup"


class User(msgspec.Struct):
    id: int
    first_name: str


class Message(msgspec.Struct):
    message_id: int
    chat: Chat
    text: str | None = None
    reply_to_message: "Message | None" = None
    from_: User | None = msgspec.field(name="from", default=None)


class CallbackQuery(msgspec.Struct):
    id: str
    from_: User = msgspec.field(name="from")
    message: Message | None = None
    data: str | None = None


class Update(msgspec.Struct):
    update_id: int
    message: Message | None = None
    callback_query: CallbackQuery | None = None


class ResponseParameters(msgspec.Struct):
    retry_after: int | None = None


class BotApiResponse(msgspec.Struct):
    ok: bool
    result: msgspec.Raw | None = None
    error_code: int | None = None
    description: str | None = None
    parameters: ResponseParameters | None = None


# Outgoing types

class InlineKeyboardButton(msgspec.Struct):
    text: str
    callback_data: str


class InlineKeyboardMarkup(msgspec.Struct):
    inline_keyboard: list[list[InlineKeyboardButton]]


# Exceptions

class RetryAfter(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Rate limited, retry after {retry_after}s")


class BotApiError(Exception):
    def __init__(self, code: int, description: str | None):
        self.code = code
        self.description = description
        super().__init__(f"Telegram API error {code}: {description}")
```

---

## 7. `callbacks.py` (Callback Data Encoding)

Encodes/decodes callback data for inline keyboard buttons. Telegram limits callback data to 64 bytes.

```python
ACTIONS = ("approve", "reject", "feedback")

# Format: "adl:{action}:{issue_id}:{stage_ref}"
# Example: "adl:approve:42:security"

def encode_callback(action: str, issue_id: int, stage_ref: str) -> str:
    assert action in ACTIONS
    data = f"adl:{action}:{issue_id}:{stage_ref}"
    assert len(data.encode()) <= 64, f"Callback data too long: {len(data.encode())} bytes"
    return data

def decode_callback(data: str) -> tuple[str, int, str] | None:
    """Returns (action, issue_id, stage_ref) or None if not an ADL callback."""
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "adl":
        return None
    action, issue_id_str, stage_ref = parts[1], parts[2], parts[3]
    if action not in ACTIONS:
        return None
    return action, int(issue_id_str), stage_ref
```

---

## 8. `messages.py` (Message Builders)

Builds the two message types: progress cards and escalation cards.

```python
from .models import InlineKeyboardButton, InlineKeyboardMarkup
from .callbacks import encode_callback

def build_progress_message(
    issue: Issue,
    workflow: WorkflowConfig,
    stage_states: dict[str, StageState],
    total_elapsed: str,
) -> str:
    """Build live-updating progress card. Returns HTML."""
    lines = [f"<b>{issue.repo} #{issue.number}</b> — {workflow.id}\n"]

    for stage in workflow.stages:
        state = stage_states.get(stage.ref)
        if state and state.status in ("approved", "completed"):
            lines.append(f"  ✅ <code>{stage.ref:<16}</code> ({state.elapsed})")
        elif state and state.status == "running":
            detail = ""
            if stage.type == "team":
                detail = f"cycle {state.iteration}/{stage.maxIterations} "
            lines.append(f"  ⏳ <code>{stage.ref:<16}</code> {detail}({state.elapsed})")
        elif state and state.status in ("vetoed", "escalated"):
            lines.append(f"  🔴 <code>{stage.ref:<16}</code> ({state.elapsed})")
        elif stage.optional:
            lines.append(f"  ⬜ <code>{stage.ref:<16}</code> (optional)")
        else:
            lines.append(f"  ⬜ <code>{stage.ref}</code>")

    lines.append(f"\nElapsed: {total_elapsed}")
    return "\n".join(lines)


def build_escalation_message(
    issue: Issue,
    stage: StageConfig,
    verdict: Verdict,
    reason: str,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build escalation card with inline action buttons. Returns (HTML, keyboard)."""
    emoji = "🔒" if reason == "security_veto" else "⚠️"
    title = reason.replace("_", " ").title()

    text = (
        f"{emoji} <b>{title}</b> — {issue.repo} #{issue.number}\n\n"
        f"Stage: <code>{stage.ref}</code>\n"
        f"Iteration: {verdict.iteration}/{stage.maxIterations}\n\n"
        f"<blockquote>{verdict.feedback or 'No feedback provided.'}</blockquote>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Approve",
            callback_data=encode_callback("approve", issue.id, stage.ref),
        ),
        InlineKeyboardButton(
            text="❌ Reject",
            callback_data=encode_callback("reject", issue.id, stage.ref),
        ),
        InlineKeyboardButton(
            text="💬 Reply",
            callback_data=encode_callback("feedback", issue.id, stage.ref),
        ),
    ]])

    return text, keyboard


def build_completion_message(issue: Issue, pr_url: str) -> str:
    """Build PR-created notification."""
    return (
        f"✅ <b>PR Created</b> — {issue.repo} #{issue.number}\n\n"
        f"<a href=\"{pr_url}\">{pr_url}</a>"
    )


def build_error_message(issue: Issue, error: str) -> str:
    """Build error notification."""
    return (
        f"🔥 <b>Error</b> — {issue.repo} #{issue.number}\n\n"
        f"<code>{error[:500]}</code>"
    )
```

---

## 9. `TelegramBot` Facade

Public API that the rest of ADL uses. Orchestrator and workflow engine call this — never the inner layers.

```python
import asyncio
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class HumanDecision:
    action: str    # "approve", "reject", "feedback", "timeout"
    feedback: str | None = None


class TelegramBot:
    """Facade for all Telegram operations. Start once, use everywhere."""

    def __init__(self, config: TelegramConfig):
        self._config = config
        api = HttpBotClient(config.bot_token)
        client = TelegramClient(api, chat_type=config.chat_type)
        self._outbox = TelegramOutbox(client)
        self._poller = TelegramPoller(api)
        self._chat_id = config.chat_id
        self._progress_messages: dict[int, int] = {}  # issue_id → message_id
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Launch background drain + poll loops."""
        self._tasks = [
            asyncio.create_task(self._outbox.drain_loop()),
            asyncio.create_task(self._poller.poll_loop()),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # --- Progress ---

    async def send_progress(
        self,
        issue: Issue,
        workflow: WorkflowConfig,
        stage_states: dict[str, StageState],
        total_elapsed: str,
    ) -> None:
        """Send or update the progress card for an issue."""
        text = build_progress_message(issue, workflow, stage_states, total_elapsed)

        if issue.id in self._progress_messages:
            msg_id = self._progress_messages[issue.id]
            await self._outbox.enqueue_edit(
                self._chat_id, msg_id, text, parse_mode="HTML",
            )
        else:
            future = await self._outbox.enqueue_send(
                self._chat_id, text, parse_mode="HTML",
            )
            msg = await future
            if msg:
                self._progress_messages[issue.id] = msg.message_id

    # --- Escalation ---

    async def escalate(
        self,
        issue: Issue,
        stage: StageConfig,
        verdict: Verdict,
        reason: str,
    ) -> HumanDecision:
        """Send escalation with inline buttons, block until human responds or timeout."""
        text, keyboard = build_escalation_message(issue, stage, verdict, reason)

        future = await self._outbox.enqueue_send(
            self._chat_id, text,
            reply_markup=msgspec.json.decode(
                msgspec.json.encode(keyboard), type=dict,
            ),
            parse_mode="HTML",
        )
        msg = await future
        if not msg:
            return HumanDecision(action="timeout", feedback="Failed to send escalation")

        # Register handlers for human response
        decision: asyncio.Future[HumanDecision] = asyncio.get_event_loop().create_future()

        async def handle_callback(cb: CallbackQuery) -> None:
            parsed = decode_callback(cb.data)
            if not parsed:
                return
            action, cb_issue_id, cb_stage_ref = parsed
            if cb_issue_id != issue.id or cb_stage_ref != stage.ref:
                return

            await self._poller._api.answer_callback_query(cb.id, text=f"Action: {action}")

            if action == "feedback":
                # Register reply handler — human will reply with text
                self._poller.on_reply_to(msg.message_id, handle_reply)
                await self._outbox.enqueue_edit(
                    self._chat_id, msg.message_id,
                    text + "\n\n<i>Reply to this message with your feedback...</i>",
                    parse_mode="HTML",
                )
            elif not decision.done():
                decision.set_result(HumanDecision(action=action))

        async def handle_reply(reply: Message) -> None:
            if not decision.done():
                decision.set_result(HumanDecision(
                    action="feedback",
                    feedback=reply.text,
                ))

        self._poller.on_callback("adl:", handle_callback)

        try:
            return await asyncio.wait_for(decision, timeout=self._config.human_timeout)
        except asyncio.TimeoutError:
            return HumanDecision(action="timeout")
        finally:
            # Cleanup: remove handlers
            self._poller._callback_handlers.pop("adl:", None)
            self._poller._reply_handlers.pop(msg.message_id, None)

    # --- Notifications ---

    async def notify_completion(self, issue: Issue, pr_url: str) -> None:
        text = build_completion_message(issue, pr_url)
        await self._outbox.enqueue_send(self._chat_id, text, parse_mode="HTML")

    async def notify_error(self, issue: Issue, error: str) -> None:
        text = build_error_message(issue, error)
        await self._outbox.enqueue_send(self._chat_id, text, parse_mode="HTML")

    def clear_progress(self, issue_id: int) -> None:
        """Remove tracked progress message after issue completes."""
        self._progress_messages.pop(issue_id, None)
```

---

## 10. Workflow Engine Integration

The `escalate_to_human` function in `workflow_engine.py` changes from file-based to callback-driven.

### Before (v3)

```python
async def escalate_to_human(issue, stage, verdict, reason):
    # Write file, send notification, hope human checks it
    write_json(worktree / "needs_human.json", {
        "issue_id": issue.id,
        "reason": reason,
        "stage": stage.ref,
        "context": verdict.feedback,
    })
    await notify_telegram(issue, reason)
    # ??? no feedback path
```

### After (this amendment)

```python
async def escalate_to_human(
    issue: Issue,
    stage: StageConfig,
    verdict: Verdict,
    reason: str,
    telegram: TelegramBot,
) -> Verdict:
    """Escalate to human via Telegram. Blocks until response or timeout."""
    decision = await telegram.escalate(issue, stage, verdict, reason)

    match decision.action:
        case "approve":
            return Verdict(status="approved")
        case "reject":
            return Verdict(status="needs_revision", feedback="Human rejected.")
        case "feedback":
            return Verdict(status="needs_revision", feedback=decision.feedback)
        case "timeout":
            raise HumanTimeoutError(issue, stage)
```

### Progress reporting hook

The workflow engine loop gains a progress callback:

```python
async def execute_workflow(
    workflow: WorkflowConfig,
    issue: Issue,
    worktree: Path,
    telegram: TelegramBot,       # NEW
) -> WorkflowResult:
    stage_states: dict[str, StageState] = {}
    start_time = time.monotonic()

    for stage in workflow.stages:
        # ... existing skip/condition logic ...

        stage_states[stage.ref] = StageState(status="running", started_at=time.monotonic())

        # Report progress
        elapsed = format_elapsed(time.monotonic() - start_time)
        await telegram.send_progress(issue, workflow, stage_states, elapsed)

        # ... dispatch stage, parse verdict ...

        stage_states[stage.ref] = StageState(
            status=verdict.status,
            elapsed=format_elapsed(time.monotonic() - stage_states[stage.ref].started_at),
            iteration=iteration,
        )

        # Update progress after stage completes
        elapsed = format_elapsed(time.monotonic() - start_time)
        await telegram.send_progress(issue, workflow, stage_states, elapsed)

    telegram.clear_progress(issue.id)
    return WorkflowResult(status="completed")
```

---

## 11. Observability

New Telegram-specific metrics added to the per-issue log:

```python
@dataclass
class TelegramMetrics:
    messages_sent: int = 0
    messages_edited: int = 0
    edits_coalesced: int = 0     # Edits that were merged by outbox
    retries: int = 0             # RetryAfter backoffs
    callbacks_received: int = 0
    escalations_sent: int = 0
    escalation_response_time: float | None = None  # Seconds until human responded
```

Logged to `log.jsonl` at issue completion. Useful for tuning progress update frequency and measuring human response times.

---

## 12. Changes to v3 Design Doc

| v3 Section | Change |
|------------|--------|
| §2 Technology Choices | Replace `Telegram: Raw httpx + long-polling` → `Telegram: httpx + layered client (HttpBotClient → TelegramClient → TelegramOutbox → TelegramPoller), msgspec models, interactive inline keyboards. Patterns from banteg/takopi.` |
| §7.8 Telegram Bot | Replace "Unchanged from v2" → reference this document |
| §7.9 Observability | Add `TelegramMetrics` to per-issue logs |
| §12 Project Structure | Replace `telegram.py` → `telegram/` package (8 files) |
| §13 Dependencies | Add `msgspec` to dependencies |
| §14 Configuration | Expand `telegram:` section (see below) |
| Workflow §5 | `escalate_to_human()` returns `HumanDecision` via inline keyboard, not file |

### Updated Configuration

```yaml
telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN}"
  chat_id: "${TELEGRAM_CHAT_ID}"
  chat_type: "private"          # NEW: "private" or "group" (affects rate limits)
  human_timeout: 3600           # Seconds to wait for human response
  progress_updates: true        # NEW: send live progress cards
```

### Updated Project Structure

```
src/auto_dev_loop/
  telegram/                     # CHANGED: was telegram.py
    __init__.py
    bot_api.py                  # Layer 1: raw httpx → Bot API
    client.py                   # Layer 2: rate-limited wrapper
    outbox.py                   # Layer 3: priority queue + coalescing
    poller.py                   # Layer 4: long-polling + routing
    models.py                   # msgspec Telegram types
    messages.py                 # progress/escalation message builders
    callbacks.py                # callback data encode/decode
tests/
  test_telegram_outbox.py       # NEW: coalescing + priority tests
  test_telegram_poller.py       # NEW: routing tests
  test_telegram_callbacks.py    # NEW: encode/decode tests
  test_telegram_messages.py     # NEW: message builder tests
```

### Updated Dependencies

```toml
[project]
dependencies = [
    "claude-agent-sdk",
    "httpx",
    "msgspec",           # NEW: fast Telegram API serialization
    "pyyaml",
    "typer",
    "aiosqlite",
    "aiofiles",
]
```

### Removed

- `needs_human.json` file-based escalation pattern
- File-watching trigger for `feedback_applier.md` agent

---

## 13. Attribution

- **Layered client architecture:** [banteg/takopi](https://github.com/banteg/takopi) `telegram/client_api.py` → `client.py` → `outbox.py`
- **Priority outbox with edit coalescing:** takopi `telegram/outbox.py`
- **Per-chat rate limiter:** takopi `telegram/client.py`
- **RetryAfter exception pattern:** takopi `telegram/client_api.py`
- **msgspec for Telegram models:** takopi throughout
- **Long-polling with dedup:** takopi `telegram/loop.py`
- **Inline keyboard for interactive decisions:** Adapted from takopi's cancel-button pattern
