"""Telegram client package — TelegramBot facade is the public API."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx
import msgspec

from ..models import Issue, StageState, TelegramConfig
from ..state import StateStore
from ..workflow_loader import WorkflowConfig, StageConfig
from .bot_api import HttpBotClient
from .callbacks import decode_callback
from .client import TelegramClient
from .messages import (
    build_completion_message,
    build_error_message,
    build_escalation_message,
    build_progress_message,
    build_security_message,
)
from .models import BotApiError, CallbackQuery, Message, RetryAfter
from .outbox import TelegramOutbox
from .poller import TelegramPoller

log = logging.getLogger(__name__)


def _suppress_exception(future: asyncio.Future) -> None:
    """Consume exception on fire-and-forget futures to avoid 'exception was never retrieved' warnings."""
    if future.cancelled():
        return
    future.exception()  # marks exception as retrieved


@dataclass
class HumanDecision:
    action: str    # "approve", "reject", "feedback", "timeout"
    feedback: str | None = None


class TelegramBot:
    """Facade for all Telegram operations. Start once, use everywhere."""

    def __init__(self, config: TelegramConfig, store: StateStore | None = None):
        if config.use_topics and store is None:
            raise ValueError("StateStore is required when Telegram topics are enabled")
        self._config = config
        self._api = HttpBotClient(config.bot_token)
        client = TelegramClient(self._api, chat_type=config.chat_type)
        self._outbox = TelegramOutbox(client)
        self._poller = TelegramPoller(self._api)
        self._chat_id = config.chat_id
        self._progress_messages: dict[int, int] = {}
        self._tasks: list[asyncio.Task] = []
        self._store = store
        self._thread_cache: dict[str, int | None] = {}
        self._thread_locks: dict[str, asyncio.Lock] = {}

    async def _resolve_thread_id(self, repo: str) -> int | None:
        """Return the forum topic thread ID for *repo*, or None if topics disabled."""
        if not self._config.use_topics:
            return None
        if repo in self._thread_cache:
            return self._thread_cache[repo]  # may be None (cached failure)
        lock = self._thread_locks.setdefault(repo, asyncio.Lock())
        async with lock:
            try:
                if repo in self._thread_cache:
                    return self._thread_cache[repo]
                if self._store:
                    stored = await self._store.get_thread_id(repo)
                    if stored is not None:
                        self._thread_cache[repo] = stored
                        return stored
                result = await self._api.create_forum_topic(self._chat_id, repo)
                thread_id = result.message_thread_id
                self._thread_cache[repo] = thread_id
                if self._store:
                    try:
                        await self._store.store_thread_id(repo, thread_id)
                    except Exception as db_exc:
                        log.warning("Failed to persist thread ID for %s: %s", repo, db_exc)
                return thread_id
            except RetryAfter as exc:
                log.warning(
                    "Rate-limited creating topic for %s, retrying after %ss",
                    repo, exc.retry_after,
                )
                await asyncio.sleep(exc.retry_after)
                try:
                    result = await self._api.create_forum_topic(self._chat_id, repo)
                except (BotApiError, httpx.HTTPError, OSError, ValueError) as retry_exc:
                    log.warning("Retry failed for %s: %s", repo, retry_exc)
                    self._thread_cache[repo] = None
                    return None
                thread_id = result.message_thread_id
                self._thread_cache[repo] = thread_id
                if self._store:
                    try:
                        await self._store.store_thread_id(repo, thread_id)
                    except Exception as db_exc:
                        log.warning("Failed to persist thread ID for %s: %s", repo, db_exc)
                return thread_id
            except (BotApiError, httpx.HTTPError, OSError, ValueError) as exc:
                log.warning(
                    "Failed to create forum topic for %s, sending without topic: %s",
                    repo, exc,
                )
                self._thread_cache[repo] = None
                return None

    async def _thread_kwargs(self, issue: Issue | None) -> dict:
        """Return {"message_thread_id": N} if topics enabled, else {}."""
        if issue is None:
            return {}
        thread_id = await self._resolve_thread_id(issue.repo)
        if thread_id is not None:
            return {"message_thread_id": thread_id}
        return {}

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
        await self._api.close()

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
            tkw = await self._thread_kwargs(issue)
            future = await self._outbox.enqueue_send(
                self._chat_id, text, parse_mode="HTML", **tkw,
            )
            try:
                msg = await future
            except Exception:
                log.warning("Failed to send progress message for issue %s", issue.id)
                return
            if msg:
                self._progress_messages[issue.id] = msg.message_id

    # --- Escalation ---

    async def escalate(
        self,
        issue: Issue,
        stage: StageConfig,
        verdict: object,
        reason: str,
    ) -> HumanDecision:
        """Send escalation with inline buttons, block until human responds or timeout."""
        text, keyboard = build_escalation_message(issue, stage, verdict, reason)
        tkw = await self._thread_kwargs(issue)

        future = await self._outbox.enqueue_send(
            self._chat_id, text,
            reply_markup=msgspec.json.decode(
                msgspec.json.encode(keyboard), type=dict,
            ),
            parse_mode="HTML",
            **tkw,
        )
        try:
            msg = await future
        except Exception as exc:
            return HumanDecision(action="timeout", feedback=f"Failed to send escalation: {exc}")
        if not msg:
            return HumanDecision(action="timeout", feedback="Failed to send escalation")

        decision: asyncio.Future[HumanDecision] = asyncio.get_running_loop().create_future()

        async def handle_callback(cb: CallbackQuery) -> None:
            if cb.message is None or cb.message.chat.id != self._chat_id:
                await self._api.answer_callback_query(cb.id, text="Unauthorized")
                return
            parsed = decode_callback(cb.data)
            if not parsed:
                return
            action, cb_issue_id, cb_stage_ref = parsed
            if cb_issue_id != issue.id or cb_stage_ref != stage.ref:
                return

            await self._api.answer_callback_query(cb.id, text=f"Action: {action}")

            if action == "feedback":
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

        handler_id = f"esc:{issue.id}:{stage.ref}"
        self._poller.on_callback(handler_id, "adl:", handle_callback)

        try:
            return await asyncio.wait_for(decision, timeout=self._config.human_timeout)
        except asyncio.TimeoutError:
            return HumanDecision(action="timeout")
        finally:
            self._poller.remove_callback(handler_id)
            self._poller.remove_reply_handler(msg.message_id)

    # --- Notifications ---

    async def _fire_and_forget(self, text: str, issue: Issue | None) -> None:
        """Enqueue a message with thread routing and suppress send errors."""
        tkw = await self._thread_kwargs(issue)
        future = await self._outbox.enqueue_send(self._chat_id, text, parse_mode="HTML", **tkw)
        future.add_done_callback(_suppress_exception)

    async def notify_completion(self, issue: Issue, pr_url: str) -> None:
        await self._fire_and_forget(build_completion_message(issue, pr_url), issue)

    async def notify_error(self, issue: Issue, error: str) -> None:
        await self._fire_and_forget(build_error_message(issue, error), issue)

    async def notify_security(
        self,
        issue: Issue | None,
        blocked_commands: list[dict],
    ) -> None:
        """Send a security alert for blocked commands. Awaits delivery."""
        if not blocked_commands:
            return
        tkw = await self._thread_kwargs(issue)
        future = await self._outbox.enqueue_send(
            self._chat_id,
            build_security_message(issue, blocked_commands),
            parse_mode="HTML",
            **tkw,
        )
        try:
            await future
        except Exception as exc:
            log.warning("Failed to deliver security alert: %s", exc)

    def clear_progress(self, issue_id: int) -> None:
        """Remove tracked progress message after issue completes."""
        self._progress_messages.pop(issue_id, None)
