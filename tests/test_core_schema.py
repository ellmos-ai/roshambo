"""The vector index op class guard.

This is the one schema mistake that costs nothing in correctness and everything in
performance, and it cannot be caught by reading the schema file: `CREATE TABLE IF NOT
EXISTS` is a no-op against a cluster whose tables already exist, so an index built by an
older revision keeps its op class and no statement ever complains. These tests build the
broken state on purpose and check that it is detected and repaired.
"""

from __future__ import annotations

import uuid

import pytest

from roshambo.config import RoshamboConfig
from roshambo.db import (
    VECTOR_INDEXES,
    VectorIndexSpec,
    apply_schema,
    connect,
    find_vector_index_mismatches,
    repair_vector_index,
    vector_index_op_class,
)
from roshambo.errors import SchemaError

pytestmark = pytest.mark.live


@pytest.fixture
def scratch_table(cfg: RoshamboConfig):
    """A throwaway table carrying a deliberately wrong `vector_l2_ops` index.

    Built against the live cluster rather than mocked: the thing under test is what
    CockroachDB reports back about an index it created, which a fake cannot tell us.
    """
    name = f"scratch_{uuid.uuid4().hex[:12]}"
    with connect(cfg) as conn:
        conn.execute(
            f"CREATE TABLE {name} ("
            "  swarm_id STRING NOT NULL,"
            "  row_id UUID NOT NULL DEFAULT gen_random_uuid(),"
            "  embedding VECTOR(8) NOT NULL,"
            "  PRIMARY KEY (swarm_id, row_id),"
            f"  VECTOR INDEX {name}_idx (swarm_id, embedding vector_l2_ops)"
            ")"
        )
        try:
            yield name, conn
        finally:
            conn.execute(f"DROP TABLE IF EXISTS {name}")


def test_a_wrong_op_class_is_reported(scratch_table):
    name, conn = scratch_table
    spec = VectorIndexSpec(name, f"{name}_idx", "embedding", "vector_cosine_ops")

    assert vector_index_op_class(conn, spec) == "vector_l2_ops"
    mismatches = find_vector_index_mismatches(conn, [spec])
    assert mismatches == [(spec, "vector_l2_ops")]


def test_repair_replaces_the_index_with_the_right_op_class(scratch_table):
    name, conn = scratch_table
    spec = VectorIndexSpec(name, f"{name}_idx", "embedding", "vector_cosine_ops")

    repair_vector_index(conn, spec)

    assert vector_index_op_class(conn, spec) == "vector_cosine_ops"
    assert find_vector_index_mismatches(conn, [spec]) == []


def test_a_missing_index_is_reported_as_missing_not_as_matching(scratch_table):
    """`None` must not compare equal to "the op class we wanted"."""
    name, conn = scratch_table
    conn.execute(f"DROP INDEX {name}@{name}_idx")
    spec = VectorIndexSpec(name, f"{name}_idx", "embedding", "vector_cosine_ops")

    assert find_vector_index_mismatches(conn, [spec]) == [(spec, None)]


def test_apply_schema_leaves_the_real_indexes_matching(cfg: RoshamboConfig):
    """After the fixture has applied the schema, production indexes must be clean."""
    with connect(cfg) as conn:
        assert find_vector_index_mismatches(conn, VECTOR_INDEXES) == []


def test_apply_schema_refuses_to_finish_quietly_on_a_mismatch(
    cfg: RoshamboConfig, monkeypatch: pytest.MonkeyPatch, scratch_table
):
    """Without `repair_vector_indexes`, a mismatch has to be an error, not a warning.

    The whole point of the check is that this failure mode is otherwise silent.
    """
    name, _conn = scratch_table
    spec = VectorIndexSpec(name, f"{name}_idx", "embedding", "vector_cosine_ops")
    monkeypatch.setattr("roshambo.db.VECTOR_INDEXES", (spec,))

    with pytest.raises(SchemaError, match="op class"):
        apply_schema(cfg)


def test_apply_schema_repairs_when_asked(
    cfg: RoshamboConfig, monkeypatch: pytest.MonkeyPatch, scratch_table
):
    name, conn = scratch_table
    spec = VectorIndexSpec(name, f"{name}_idx", "embedding", "vector_cosine_ops")
    monkeypatch.setattr("roshambo.db.VECTOR_INDEXES", (spec,))

    results = apply_schema(cfg, repair_vector_indexes=True)

    assert any(status == "repaired" for status, _ in results)
    assert vector_index_op_class(conn, spec) == "vector_cosine_ops"


def test_index_names_are_constrained_to_plain_identifiers(cfg: RoshamboConfig):
    """`DROP INDEX` cannot bind parameters, so the identifier path must be closed."""
    bad = VectorIndexSpec(
        table="trails",
        index='x"; DROP TABLE trails; --',
        column="embedding",
        op_class="vector_cosine_ops",
    )
    with connect(cfg) as conn:
        with pytest.raises(SchemaError, match="identifier"):
            repair_vector_index(conn, bad)
