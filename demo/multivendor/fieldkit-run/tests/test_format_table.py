import pytest
from fieldkit.format_table import format_table


def test_format_table_examples():
    rows = [["a", "bb"], ["ccc", "d"]]
    assert format_table(rows) == "a    bb\nccc  d "


def test_format_table_empty():
    assert format_table([]) == ""


def test_format_table_single_row():
    assert format_table([["hello", "world"]]) == "hello  world"
