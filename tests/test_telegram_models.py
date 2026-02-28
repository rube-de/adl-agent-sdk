"""Tests for Telegram msgspec models."""

import msgspec

from auto_dev_loop.telegram.models import (
    Chat, User, Message, CallbackQuery, Update,
    BotApiResponse, ResponseParameters,
    InlineKeyboardButton, InlineKeyboardMarkup,
    RetryAfter, BotApiError,
)


def test_message_decode():
    raw = b'{"message_id": 1, "chat": {"id": 123, "type": "private"}}'
    msg = msgspec.json.decode(raw, type=Message)
    assert msg.message_id == 1
    assert msg.chat.id == 123


def test_message_optional_fields():
    raw = b'{"message_id": 1, "chat": {"id": 1, "type": "private"}}'
    msg = msgspec.json.decode(raw, type=Message)
    assert msg.text is None
    assert msg.reply_to_message is None


def test_callback_query_from_field():
    raw = b'{"id": "abc", "from": {"id": 1, "first_name": "Test"}}'
    cb = msgspec.json.decode(raw, type=CallbackQuery)
    assert cb.from_.id == 1


def test_update_with_message():
    raw = b'{"update_id": 1, "message": {"message_id": 1, "chat": {"id": 1, "type": "private"}}}'
    upd = msgspec.json.decode(raw, type=Update)
    assert upd.message is not None
    assert upd.callback_query is None


def test_update_with_callback():
    raw = b'{"update_id": 2, "callback_query": {"id": "x", "from": {"id": 1, "first_name": "U"}}}'
    upd = msgspec.json.decode(raw, type=Update)
    assert upd.callback_query is not None
    assert upd.message is None


def test_bot_api_response_ok():
    raw = b'{"ok": true, "result": [1, 2, 3]}'
    resp = msgspec.json.decode(raw, type=BotApiResponse)
    assert resp.ok is True


def test_bot_api_response_error():
    raw = b'{"ok": false, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 30}}'
    resp = msgspec.json.decode(raw, type=BotApiResponse)
    assert resp.ok is False
    assert resp.parameters.retry_after == 30


def test_inline_keyboard_encode():
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="OK", callback_data="yes"),
    ]])
    data = msgspec.json.encode(kb)
    decoded = msgspec.json.decode(data)
    assert decoded["inline_keyboard"][0][0]["text"] == "OK"


def test_retry_after_exception():
    exc = RetryAfter(30)
    assert exc.retry_after == 30


def test_bot_api_error():
    exc = BotApiError(400, "Bad Request")
    assert exc.code == 400
