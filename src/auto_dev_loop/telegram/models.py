"""Minimal msgspec structs for the Telegram Bot API."""

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
    result: msgspec.Raw = msgspec.Raw(b"null")
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
