import pytest

from fieldkit.clamp import clamp


def test_clamp_examples():
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(11, 0, 10) == 10


def test_clamp_rejects_inverted_range():
    with pytest.raises(ValueError):
        clamp(5, 10, 0)
