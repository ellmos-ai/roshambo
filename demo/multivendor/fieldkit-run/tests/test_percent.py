from fieldkit.percent import percent


def test_exact_quarter():
    assert percent(1, 4) == 25.0


def test_rounds_to_one_digit_by_default():
    assert percent(1, 3) == 33.3


def test_honours_digits_argument():
    assert percent(2, 3, digits=2) == 66.67


def test_whole_of_zero_returns_zero():
    assert percent(5, 0) == 0.0
