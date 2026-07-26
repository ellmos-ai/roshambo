from datetime import datetime


def is_expired(expires_at: datetime, now: datetime) -> bool:
    """Report whether a lease has lapsed."""
    if expires_at.tzinfo is None or expires_at.tzinfo.utcoffset(expires_at) is None:
        raise ValueError("expires_at must be timezone-aware")
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be timezone-aware")
    return expires_at <= now
