from fieldkit.backoff_delays import backoff_delays


def test_default_delays() -> None:
    assert backoff_delays(4) == [1.0, 2.0, 4.0, 8.0]


def test_custom_base() -> None:
    assert backoff_delays(3, base=0.5) == [0.5, 1.0, 2.0]


def test_delays_are_capped() -> None:
    assert backoff_delays(5, base=10, cap=25) == [
        10.0,
        20.0,
        25.0,
        25.0,
        25.0,
    ]


def test_zero_attempts_returns_empty_list() -> None:
    assert backoff_delays(0) == []
