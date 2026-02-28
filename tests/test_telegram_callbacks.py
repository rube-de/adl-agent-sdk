"""Tests for Telegram callback data encoding/decoding."""

import pytest

from auto_dev_loop.telegram.callbacks import encode_callback, decode_callback, ACTIONS


def test_encode_approve():
    data = encode_callback("approve", 42, "security")
    assert data == "adl:approve:42:security"


def test_encode_reject():
    data = encode_callback("reject", 1, "plan_review")
    assert data == "adl:reject:1:plan_review"


def test_encode_feedback():
    data = encode_callback("feedback", 99, "dev")
    assert data == "adl:feedback:99:dev"


def test_encode_invalid_action():
    with pytest.raises(AssertionError):
        encode_callback("invalid", 1, "plan")


def test_encode_fits_64_bytes():
    data = encode_callback("approve", 999999, "multi_review")
    assert len(data.encode()) <= 64


def test_decode_valid():
    result = decode_callback("adl:approve:42:security")
    assert result == ("approve", 42, "security")


def test_decode_reject():
    result = decode_callback("adl:reject:1:plan")
    assert result == ("reject", 1, "plan")


def test_decode_non_adl_prefix():
    assert decode_callback("other:approve:1:plan") is None


def test_decode_wrong_part_count():
    assert decode_callback("adl:approve:1") is None


def test_decode_invalid_action():
    assert decode_callback("adl:invalid:1:plan") is None
