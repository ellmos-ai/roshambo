import pytest
from fieldkit.truncate_middle import truncate_middle

def test_truncate_middle_examples():
    assert truncate_middle("abcdefghij", 10) == "abcdefghij"
    assert truncate_middle("abcdefghij", 7) == "abc…hij"

def test_truncate_middle_edge_case():
    with pytest.raises(ValueError):
        truncate_middle("abcdefghij", 2)
