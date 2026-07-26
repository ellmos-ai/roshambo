def clamp(value: float, low: float, high: float) -> float:
    """Restrict a value to the inclusive range from low to high."""
    if low > high:
        raise ValueError("low must not be greater than high")
    return min(max(value, low), high)
