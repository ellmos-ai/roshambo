"""Database plumbing: connections, schema application, retry on serialization errors.

Roshambo talks to CockroachDB over the PostgreSQL wire protocol with psycopg v3.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import psycopg

from .config import RoshamboConfig
from .errors import SchemaError

T = TypeVar("T")

SCHEMA_FILENAME = "001_init.sql"
SCHEMA_ENV_VAR = "ROSHAMBO_SCHEMA_PATH"

#: CockroachDB reports failed serializable transactions with SQLSTATE 40001. Single
#: statements in implicit transactions are retried by the server, but a client that
#: opens explicit transactions has to be prepared to retry them itself.
SERIALIZATION_FAILURE = "40001"

_RETRY_BASE_DELAY = 0.02
_RETRY_MAX_ATTEMPTS = 8


def connect(cfg: RoshamboConfig, *, autocommit: bool = True) -> psycopg.Connection:
    """Open one connection.

    A `Roshambo` instance owns exactly one connection and is therefore **not** thread safe:
    every concurrent agent gets its own instance and its own connection. That is
    deliberate — the concurrency guarantees under test are the database's, and faking
    them with a shared client-side connection would not exercise anything.
    """
    conn = psycopg.connect(cfg.dsn, autocommit=autocommit)
    return conn


def retry_on_serialization_failure(
    fn: Callable[[], T], *, max_attempts: int = _RETRY_MAX_ATTEMPTS
) -> T:
    """Run `fn`, retrying with backoff while CockroachDB reports 40001.

    Losing a serializable race is not a failure of the caller's logic; it is the
    database telling us to try again. Anything else propagates untouched.
    """
    delay = _RETRY_BASE_DELAY
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except psycopg.errors.SerializationFailure:
            if attempt == max_attempts:
                raise
            time.sleep(delay)
            delay *= 2
        except psycopg.Error as exc:
            if getattr(exc, "sqlstate", None) != SERIALIZATION_FAILURE:
                raise
            if attempt == max_attempts:
                raise
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


def to_vector_literal(values: Sequence[float]) -> str:
    """Render an embedding in the text form CockroachDB accepts for `VECTOR`."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


# --------------------------------------------------------------------------- schema


def find_schema_file(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Locate `schema/001_init.sql`.

    Search order: explicit argument, `ROSHAMBO_SCHEMA_PATH`, then the `schema/` directory
    of a source checkout relative to this module.
    """
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    env_path = os.environ.get(SCHEMA_ENV_VAR)
    if env_path:
        candidates.append(Path(env_path))

    here = Path(__file__).resolve()
    # src/roshambo/db.py -> src/roshambo -> src -> <repo root>
    candidates.append(here.parents[2] / "schema" / SCHEMA_FILENAME)
    # installed layout, if the schema is shipped next to the package
    candidates.append(here.parent / "schema" / SCHEMA_FILENAME)

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    tried = ", ".join(str(c) for c in candidates)
    raise SchemaError(
        f"could not find {SCHEMA_FILENAME}. Pass an explicit path or set "
        f"{SCHEMA_ENV_VAR}. Tried: {tried}"
    )


def split_statements(sql: str) -> list[str]:
    """Split a SQL script into statements.

    Naive splitting on ``;`` is wrong here: the schema file contains prose comments that
    themselves contain semicolons. Line comments are stripped first, and quoting is
    tracked so a semicolon inside a string literal never ends a statement.
    """
    statements: list[str] = []
    buf: list[str] = []
    in_single = False
    in_line_comment = False
    i = 0
    n = len(sql)

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                buf.append(ch)
            i += 1
            continue

        if in_single:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":  # escaped quote
                    buf.append(nxt)
                    i += 2
                    continue
                in_single = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            in_line_comment = True
            i += 2
            continue

        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue

        if ch == ";":
            statement = "".join(buf).strip()
            if statement:
                statements.append(statement)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def apply_schema(
    cfg: RoshamboConfig,
    schema_path: str | os.PathLike[str] | None = None,
    *,
    repair_vector_indexes: bool = False,
) -> list[tuple[str, str]]:
    """Apply the schema. Returns one `(status, first line of statement)` pair each.

    Status is ``ok``, or ``skipped: <reason>`` for statements a given CockroachDB
    release legitimately rejects. Exactly one such case is tolerated: the
    `feature.vector_index.enabled` cluster setting, which only exists on the releases
    where vector indexes were still a gated preview. Every other error is raised.

    Afterwards the vector indexes are verified against `VECTOR_INDEXES`. A mismatch
    raises `SchemaError` unless `repair_vector_indexes` is set, in which case the
    offending index is dropped and rebuilt. See `find_vector_index_mismatches` for why
    applying the schema is not by itself enough to guarantee the right op class.
    """
    path = find_schema_file(schema_path)
    statements = split_statements(path.read_text(encoding="utf-8"))
    results: list[tuple[str, str]] = []

    with connect(cfg) as conn:
        for statement in statements:
            label = statement.splitlines()[0].strip()[:100]
            try:
                conn.execute(statement)  # type: ignore[arg-type]
                results.append(("ok", label))
            except psycopg.Error as exc:
                if _is_tolerable_setting_error(statement, exc):
                    results.append((f"skipped: {_short(exc)}", label))
                    continue
                if _is_duplicate_identity_constraint(statement, exc):
                    results.append((f"skipped: {_short(exc)}", label))
                    continue
                raise SchemaError(f"failed to apply {label!r}: {exc}") from exc

        mismatches = find_vector_index_mismatches(conn)
        if mismatches and not repair_vector_indexes:
            raise SchemaError(_mismatch_report(mismatches))
        for spec, _found in mismatches:
            repair_vector_index(conn, spec)
            results.append(("repaired", f"VECTOR INDEX {spec.index} ON {spec.table}"))
    return results


# ------------------------------------------------------------- vector index guard


@dataclass(frozen=True)
class VectorIndexSpec:
    """A vector index the code depends on, and the op class it must carry."""

    table: str
    index: str
    column: str
    op_class: str
    prefix: str = "swarm_id"


#: `recall()` orders by the cosine operator `<=>`, and CockroachDB only accelerates that
#: with a `vector_cosine_ops` index. The pairing is checked rather than assumed because
#: getting it wrong costs correctness nothing and performance everything.
VECTOR_INDEXES: tuple[VectorIndexSpec, ...] = (
    VectorIndexSpec("trails", "trails_by_swarm", "embedding", "vector_cosine_ops"),
    VectorIndexSpec("facts", "facts_by_swarm", "embedding", "vector_cosine_ops"),
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _identifier(name: str) -> str:
    """Reject anything that is not a bare SQL identifier.

    Index and table names reach `SHOW CREATE TABLE` and `DROP INDEX` by string
    interpolation — they cannot be bound as parameters — so they are constrained here
    even though every current caller passes a module-level constant.
    """
    if not _IDENTIFIER_RE.match(name):
        raise SchemaError(f"not a plain SQL identifier: {name!r}")
    return name


def vector_index_op_class(conn: psycopg.Connection, spec: VectorIndexSpec) -> str | None:
    """Op class the cluster actually has for `spec`, or None if the index is absent.

    Read out of `SHOW CREATE TABLE` because that is where CockroachDB renders the op
    class; `information_schema` and `SHOW INDEXES` both omit it (v25.4.0).
    """
    table = _identifier(spec.table)
    row = fetch_one(conn, f"SELECT create_statement FROM [SHOW CREATE TABLE {table}]", ())
    if row is None:
        return None
    pattern = re.compile(
        rf"VECTOR\s+INDEX\s+{re.escape(spec.index)}\s*\([^)]*?"
        rf"{re.escape(spec.column)}\s+(\w+)\s*\)",
        re.IGNORECASE,
    )
    match = pattern.search(row[0])
    return match.group(1) if match else None


def find_vector_index_mismatches(
    conn: psycopg.Connection, specs: Sequence[VectorIndexSpec] | None = None
) -> list[tuple[VectorIndexSpec, str | None]]:
    """Vector indexes whose op class is not what the code needs.

    This exists because the schema is written with `CREATE TABLE IF NOT EXISTS`, which
    is idempotent in the wrong direction: on a cluster where the table already exists it
    silently keeps whatever index that table was created with, including an op class
    from an older revision of this file. The resulting query still returns correct rows —
    it just stops using the index — so nothing fails and nothing is logged. Measured on
    v25.4.0 with 200 rows in one swarm: `<->` against a `vector_l2_ops` index planned a
    `vector search`, `<=>` against the same index planned a full `scan` of the primary
    key. Same table, same data, operator alone. See docs/EVIDENCE-core.md.

    Tables that do not exist yet are not reported: that is a schema that has not been
    applied, not an index with the wrong op class.

    `specs` defaults to the module-level `VECTOR_INDEXES` and is resolved on every call
    rather than bound as a default argument, so tests can point the check at a scratch
    table.
    """
    specs = VECTOR_INDEXES if specs is None else specs
    mismatches: list[tuple[VectorIndexSpec, str | None]] = []
    for spec in specs:
        try:
            found = vector_index_op_class(conn, spec)
        except psycopg.errors.UndefinedTable:
            continue
        if found != spec.op_class:
            mismatches.append((spec, found))
    return mismatches


def repair_vector_index(conn: psycopg.Connection, spec: VectorIndexSpec) -> None:
    """Drop and rebuild one vector index with the op class the code needs.

    Opt-in (`apply_schema(..., repair_vector_indexes=True)` or `roshambo init-schema
    --repair-vector-indexes`) rather than automatic: rebuilding a vector index on a
    populated cluster is real work, and doing it as a silent side effect of "apply the
    schema" is the kind of surprise DDL nobody wants from a library.
    """
    table = _identifier(spec.table)
    index = _identifier(spec.index)
    column = _identifier(spec.column)
    prefix = _identifier(spec.prefix)
    op_class = _identifier(spec.op_class)
    conn.execute(f"DROP INDEX IF EXISTS {table}@{index}")  # type: ignore[arg-type]
    conn.execute(  # type: ignore[arg-type]
        f"CREATE VECTOR INDEX {index} ON {table} ({prefix}, {column} {op_class})"
    )


def _mismatch_report(mismatches: Sequence[tuple[VectorIndexSpec, str | None]]) -> str:
    lines = [
        "vector index op class does not match what this code queries with.",
        "The queries would still return correct rows, but would stop using the index.",
        "Re-run with repair enabled (`roshambo init-schema --repair-vector-indexes`), or",
        "apply these statements by hand:",
    ]
    for spec, found in mismatches:
        state = "missing" if found is None else f"has {found}"
        lines.append(
            f"  {spec.table}.{spec.index}: {state}, needs {spec.op_class} — "
            f"DROP INDEX IF EXISTS {spec.table}@{spec.index}; "
            f"CREATE VECTOR INDEX {spec.index} ON {spec.table} "
            f"({spec.prefix}, {spec.column} {spec.op_class});"
        )
    return "\n".join(lines)


def _is_tolerable_setting_error(statement: str, exc: psycopg.Error) -> bool:
    normalised = " ".join(statement.split()).lower()
    if not normalised.startswith("set cluster setting feature.vector_index.enabled"):
        return False
    message = str(exc).lower()
    return "unknown cluster setting" in message or "unimplemented" in message


def _is_duplicate_identity_constraint(statement: str, exc: psycopg.Error) -> bool:
    """Allow only the two named, idempotent identity constraints to pre-exist."""
    normalised = " ".join(statement.split()).lower()
    expected = (
        "alter table claims add constraint claims_agent_fk",
        "alter table audit_log add constraint audit_agent_fk",
    )
    return normalised.startswith(expected) and getattr(exc, "sqlstate", None) == "42710"


def _short(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:160]


def fetch_one(conn: psycopg.Connection, sql: str, params: Sequence[Any]) -> tuple | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)  # type: ignore[arg-type]
        return cur.fetchone()


def fetch_all(conn: psycopg.Connection, sql: str, params: Sequence[Any]) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, params)  # type: ignore[arg-type]
        return cur.fetchall()


def execute(conn: psycopg.Connection, sql: str, params: Sequence[Any]) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)  # type: ignore[arg-type]
        return cur.rowcount
