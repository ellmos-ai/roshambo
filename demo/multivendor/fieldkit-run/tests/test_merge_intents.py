import pytest
from fieldkit.merge_intents import merge_intents


def test_merge_intents_basic():
    assert merge_intents(["fix bug", "fix bug", "write docs"]) == "fix bug; write docs"


def test_merge_intents_blanks():
    assert merge_intents(["a", "  ", "b"]) == "a; b"


def test_merge_intents_whitespace_stripping():
    assert merge_intents(["  fix bug ", "fix bug", " write docs"]) == "fix bug; write docs"


def test_merge_intents_empty_list():
    assert merge_intents([]) == ""
