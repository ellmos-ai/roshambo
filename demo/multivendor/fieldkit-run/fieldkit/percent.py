def percent(part: float, whole: float, digits: int = 1) -> float:
    """Return what percentage part is of whole, rounded to digits decimals."""
    if whole == 0:
        return 0.0
    return round(part / whole * 100, digits)
