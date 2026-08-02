"""Roshambo — an agentic memory fabric on CockroachDB.

A roshambo is the pile of stones walkers leave for the ones behind them: it marks the way
that works, and the point where the way ends. This package is the same idea for agent
swarms — serializable leases so two agents never take the same work, and a distributed
vector index so an agent can ask, before it starts, whether anyone has tried this and
how it ended.
"""

from __future__ import annotations

from .config import RoshamboConfig, load_config
from .errors import (
    ConfigError,
    EmbeddingError,
    RoshamboError,
    SchemaError,
    StorageError,
)
from .memory import PlaceholderEmbedder, Roshambo
from .models import (
    Claim,
    ClaimDenied,
    Confidence,
    Decision,
    Fact,
    FactKind,
    Outcome,
    Provenance,
    RecallHit,
    SwarmStatus,
    Trail,
)

__version__ = "0.1.9"

__all__ = [
    "Roshambo",
    "RoshamboConfig",
    "RoshamboError",
    "Claim",
    "ClaimDenied",
    "ConfigError",
    "Confidence",
    "Decision",
    "EmbeddingError",
    "Fact",
    "FactKind",
    "Outcome",
    "PlaceholderEmbedder",
    "Provenance",
    "RecallHit",
    "SchemaError",
    "StorageError",
    "SwarmStatus",
    "Trail",
    "__version__",
    "load_config",
]
