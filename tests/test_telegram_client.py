"""Tests for Telegram rate limiter."""

from unittest.mock import patch

import pytest

from auto_dev_loop.telegram.client import RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_no_over_refill_after_sleep():
    """Verify _last_refill is updated after sleep to prevent over-refilling.

    Bug: after sleeping in acquire(), _last_refill[chat_id] was not updated,
    causing the next acquire() to calculate excessive elapsed time and over-fill.

    Fix: add self._last_refill[chat_id] = time.monotonic() after the sleep.

    Scenario:
    - t=0.0: First acquire consumes the initial token (bucket=0)
    - t=0.6: Second acquire refills by 0.6 (bucket=0.6 < 1.0), must sleep
    - Sleep for 0.4s to accumulate one token
    - t=1.0: After sleep, _last_refill must be updated to 1.0 (not remain at 0.6)
    """
    limiter = RateLimiter(rate=1.0, burst=1)
    chat_id = 123
    mock_sleep_called = []

    # Controlled time sequence
    time_values = iter([
        0.0,      # First acquire(): now = 0.0
        0.6,      # Second acquire(): now = 0.6
        1.0,      # After sleep in second acquire(): now = 1.0
    ])

    async def mock_sleep(duration):
        """Track sleep calls but don't actually sleep."""
        mock_sleep_called.append(duration)

    def mock_monotonic():
        """Return controlled time values."""
        return next(time_values)

    with patch("time.monotonic", side_effect=mock_monotonic), \
         patch("asyncio.sleep", side_effect=mock_sleep):

        # First acquire: consume the initial token
        await limiter.acquire(chat_id)
        assert limiter._buckets[chat_id] == 0.0
        assert limiter._last_refill[chat_id] == 0.0

        # Second acquire: bucket needs refill (0.6 < 1.0), triggers sleep
        await limiter.acquire(chat_id)

        # After sleep, bucket should be 0.0 (token consumed, not over-filled)
        assert limiter._buckets[chat_id] == 0.0

        # _last_refill must be updated to the current time (1.0), not remain at 0.6
        assert limiter._last_refill[chat_id] == 1.0

        # Verify sleep was called with correct duration: (1.0 - 0.6) / 1.0 = 0.4
        assert len(mock_sleep_called) == 1
        assert abs(mock_sleep_called[0] - 0.4) < 0.01
