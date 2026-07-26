import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fieldkit.is_expired import is_expired


def test_future_expiry_is_not_expired():
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=1)
    assert is_expired(expires_at, now) is False


def test_past_expiry_is_expired():
    now = datetime.now(timezone.utc)
    expires_at = now - timedelta(seconds=1)
    assert is_expired(expires_at, now) is True


def test_equal_expiry_is_expired():
    now = datetime.now(timezone.utc)
    expires_at = now
    assert is_expired(expires_at, now) is True


def test_naive_expires_at_raises_value_error():
    now = datetime.now(timezone.utc)
    naive_expires = datetime(2026, 1, 1, 12, 0, 0)
    with pytest.raises(ValueError):
        is_expired(naive_expires, now)


def test_naive_now_raises_value_error():
    expires_at = datetime.now(timezone.utc)
    naive_now = datetime(2026, 1, 1, 12, 0, 0)
    with pytest.raises(ValueError):
        is_expired(naive_now, naive_now)
