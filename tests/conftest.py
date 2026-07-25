"""Shared test fixtures.

Tests that need a cluster are marked `live` and skip cleanly when `ROSHAMBO_DSN` is unset,
so `pytest` is green on a machine with no database. The acceptance criteria, though, are
only meaningful when these tests actually run — see docs/EVIDENCE-core.md.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roshambo.config import RoshamboConfig, load_config  # noqa: E402
from roshambo.db import apply_schema  # noqa: E402
from roshambo.memory import PlaceholderEmbedder, Roshambo  # noqa: E402

_SCHEMA_APPLIED = False


def _dsn() -> str | None:
    return os.environ.get("ROSHAMBO_DSN")


@pytest.fixture(scope="session")
def live_dsn() -> str:
    dsn = _dsn()
    if not dsn:
        pytest.skip("ROSHAMBO_DSN is not set — no live CockroachDB to test against")
    return dsn


@pytest.fixture(scope="session")
def schema_ready(live_dsn: str) -> None:
    """Apply the schema once per session. Idempotent, so re-runs are harmless.

    Repair is enabled here and nowhere else: a test cluster is very likely to carry
    tables from an earlier revision of `schema/001_init.sql`, and a vector index left
    over from one of those has an op class this code no longer queries with. Outside
    the test suite the repair stays opt-in — see `roshambo.db.repair_vector_index`.
    """
    global _SCHEMA_APPLIED
    if _SCHEMA_APPLIED:
        return
    cfg = RoshamboConfig(dsn=live_dsn, swarm_id="schema-bootstrap")
    apply_schema(cfg, repair_vector_indexes=True)
    _SCHEMA_APPLIED = True


@pytest.fixture
def swarm_id() -> str:
    """A fresh swarm per test.

    Isolation by swarm rather than by truncating tables: `swarm_id` leads every primary
    key and every vector index, so separate swarms genuinely do not see each other, and
    tests can run against a shared cluster without a teardown step.
    """
    return f"test-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def cfg(live_dsn: str, schema_ready: None, swarm_id: str) -> RoshamboConfig:
    return RoshamboConfig(dsn=live_dsn, swarm_id=swarm_id, lease_ttl_seconds=60)


@pytest.fixture
def embedder() -> PlaceholderEmbedder:
    return PlaceholderEmbedder(dim=1024)


@pytest.fixture
def roshambo(cfg: RoshamboConfig, embedder: PlaceholderEmbedder):
    client = Roshambo(cfg, embedder=embedder)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def offline_cfg() -> RoshamboConfig:
    """A config that never gets connected — for pure unit tests."""
    return RoshamboConfig(dsn="postgresql://unused@localhost:1/none", swarm_id="offline")


__all__ = ["RoshamboConfig", "Roshambo", "PlaceholderEmbedder", "load_config"]
