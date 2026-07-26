import pytest

from fieldkit.chunk import chunk


def test_splits_into_consecutive_chunks():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_empty_list_returns_empty_list():
    assert chunk([], 3) == []


def test_size_of_zero_or_less_raises():
    with pytest.raises(ValueError):
        chunk([1, 2, 3], 0)
    with pytest.raises(ValueError):
        chunk([1, 2, 3], -1)
