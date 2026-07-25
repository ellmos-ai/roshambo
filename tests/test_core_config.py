"""Configuration and value-type unit tests. No database required."""

from __future__ import annotations

import dataclasses

import pytest

from roshambo.config import RoshamboConfig, load_config
from roshambo.db import split_statements, to_vector_literal
from roshambo.errors import ConfigError


def test_load_config_reads_the_roshambo_prefix():
    cfg = load_config(
        {
            "ROSHAMBO_DSN": "postgresql://root@localhost:26257/roshambo?sslmode=disable",
            "ROSHAMBO_SWARM_ID": "demo",
            "ROSHAMBO_LEASE_TTL_SECONDS": "42",
            "ROSHAMBO_EMBEDDING_DIM": "1024",
            "ROSHAMBO_S3_BUCKET": "some-bucket",
        }
    )
    assert cfg.swarm_id == "demo"
    assert cfg.lease_ttl_seconds == 42
    assert cfg.embedding_dim == 1024
    assert cfg.s3_bucket == "some-bucket"


def test_load_config_defaults():
    cfg = load_config({"ROSHAMBO_DSN": "postgresql://x@y:1/z"})
    assert cfg.swarm_id == "default"
    assert cfg.lease_ttl_seconds == 300
    assert cfg.embedding_dim == 1024
    assert cfg.embedding_provider == "bedrock"
    assert cfg.s3_bucket is None


def test_load_config_without_dsn_is_an_error():
    with pytest.raises(ConfigError, match="ROSHAMBO_DSN"):
        load_config({})


def test_load_config_rejects_non_integer_ttl():
    with pytest.raises(ConfigError, match="LEASE_TTL_SECONDS"):
        load_config({"ROSHAMBO_DSN": "postgresql://x@y:1/z", "ROSHAMBO_LEASE_TTL_SECONDS": "soon"})


@pytest.mark.parametrize(
    ("field", "value"),
    [("swarm_id", ""), ("embedding_dim", 0), ("lease_ttl_seconds", -1)],
)
def test_config_validates_its_fields(field, value):
    kwargs = {"dsn": "postgresql://x@y:1/z", "swarm_id": "s", field: value}
    with pytest.raises(ConfigError):
        RoshamboConfig(**kwargs)


def test_config_is_frozen():
    cfg = RoshamboConfig(dsn="postgresql://x@y:1/z", swarm_id="s")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.swarm_id = "other"  # type: ignore[misc]


def test_vector_literal_round_trips_shape():
    literal = to_vector_literal([0.5, -0.25, 0.0])
    assert literal.startswith("[") and literal.endswith("]")
    assert literal.count(",") == 2


def test_statement_splitter_ignores_semicolons_in_comments():
    """The schema file contains prose comments with semicolons in them.

    A splitter that ignores this produces half-statements that fail with syntax errors,
    which is exactly the kind of failure that is hard to read at 3 a.m.
    """
    sql = """
    -- one thing; another thing
    CREATE TABLE a (x INT);
    -- trailing note; with a semicolon
    CREATE TABLE b (y STRING DEFAULT 'a;b');
    """
    statements = split_statements(sql)
    assert len(statements) == 2
    assert statements[0].startswith("CREATE TABLE a")
    assert statements[1].startswith("CREATE TABLE b")
    assert "'a;b'" in statements[1]


def test_statement_splitter_on_the_real_schema():
    from roshambo.db import find_schema_file

    statements = split_statements(find_schema_file().read_text(encoding="utf-8"))
    kinds = [s.split()[0].upper() for s in statements]
    assert kinds.count("CREATE") == 7  # 6 tables + 1 secondary index
    assert "SET" in kinds
    assert all(not s.startswith("--") for s in statements)
