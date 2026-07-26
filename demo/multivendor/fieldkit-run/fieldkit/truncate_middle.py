"""Shorten text to at most limit characters using middle ellipsis."""

def truncate_middle(text: str, limit: int) -> str:
    """Shorten text to at most limit characters by removing middle characters and placing U+2026."""
    if limit < 3:
        raise ValueError("Limit must be at least 3")
    if len(text) <= limit:
        return text
    
    # Needs 1 char for '…'
    rem = limit - 1
    left_len = rem // 2 + rem % 2
    right_len = rem // 2
    
    return text[:left_len] + "…" + text[len(text) - right_len:]
