"""Render an English ordinal for an integer."""


def ordinal(n: int) -> str:
    """Render an English ordinal for an integer."""
    if 11 <= (abs(n) % 100) <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(abs(n) % 10, "th")
    return f"{n}{suffix}"
