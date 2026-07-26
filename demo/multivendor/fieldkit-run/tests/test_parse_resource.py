import pytest

from fieldkit.parse_resource import parse_resource


def test_parse_resource_with_path() -> None:
    assert parse_resource("repo:roshambo:src/memory.py") == (
        "repo",
        "roshambo",
        "src/memory.py",
    )


def test_parse_resource_with_table() -> None:
    assert parse_resource("table:public:trails") == ("table", "public", "trails")


def test_parse_resource_preserves_colons_in_path() -> None:
    assert parse_resource("repo:roshambo:src:file.py") == (
        "repo",
        "roshambo",
        "src:file.py",
    )


@pytest.mark.parametrize("name", ["repo", "repo:roshambo"])
def test_parse_resource_rejects_fewer_than_three_parts(name: str) -> None:
    with pytest.raises(ValueError):
        parse_resource(name)
