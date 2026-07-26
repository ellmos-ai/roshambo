def backoff_delays(
    attempts: int, base: float = 1.0, cap: float = 60.0
) -> list[float]:
    """Return capped exponential delays for the requested number of attempts."""
    return [float(min(base * 2**attempt, cap)) for attempt in range(attempts)]
