def chunk(items: list, size: int) -> list[list]:
    """Split a list into consecutive chunks of at most ``size`` items."""
    if size <= 0:
        raise ValueError("size must be greater than 0")
    return [items[i:i + size] for i in range(0, len(items), size)]
