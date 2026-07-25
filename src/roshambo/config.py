"""Configuration for Roshambo.

Everything is read from the environment under the `ROSHAMBO_` prefix so that the same
code runs unchanged in a shell, in a Lambda, and inside an MCP server process.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .errors import ConfigError

ENV_PREFIX = "ROSHAMBO_"

DEFAULT_EMBEDDING_DIM = 1024
DEFAULT_LEASE_TTL_SECONDS = 300
DEFAULT_EMBEDDING_PROVIDER = "bedrock"
DEFAULT_AWS_REGION = "us-east-1"
DEFAULT_BEDROCK_MODEL_ID = "amazon.titan-embed-text-v2:0"
DEFAULT_SWARM_ID = "default"


@dataclass(frozen=True)
class RoshamboConfig:
    """Immutable runtime configuration.

    `swarm_id` is not decoration: it is the leading primary-key column of every table
    and the prefix column of every vector index, so it decides both isolation between
    swarms and whether a vector query can use the index at all.
    """

    dsn: str
    swarm_id: str
    embedding_dim: int = DEFAULT_EMBEDDING_DIM
    lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS
    embedding_provider: str = DEFAULT_EMBEDDING_PROVIDER
    aws_region: str = DEFAULT_AWS_REGION
    bedrock_model_id: str = DEFAULT_BEDROCK_MODEL_ID
    s3_bucket: str | None = None

    def __post_init__(self) -> None:
        if not self.dsn:
            raise ConfigError("dsn must not be empty")
        if not self.swarm_id:
            raise ConfigError("swarm_id must not be empty")
        if self.embedding_dim <= 0:
            raise ConfigError(f"embedding_dim must be positive, got {self.embedding_dim}")
        if self.lease_ttl_seconds <= 0:
            raise ConfigError(
                f"lease_ttl_seconds must be positive, got {self.lease_ttl_seconds}"
            )


def _get(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(ENV_PREFIX + name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _get_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{ENV_PREFIX}{name} must be an integer, got {raw!r}") from exc


def load_config(env: Mapping[str, str] | None = None) -> RoshamboConfig:
    """Build a `RoshamboConfig` from environment variables.

    Raises `ConfigError` when `ROSHAMBO_DSN` is absent — there is no sensible default for
    "which database", and silently pointing at localhost would hide misconfiguration.
    """
    env = os.environ if env is None else env

    dsn = _get(env, "DSN")
    if dsn is None:
        raise ConfigError(
            f"{ENV_PREFIX}DSN is not set. Point it at a CockroachDB cluster, e.g. "
            "postgresql://root@localhost:26257/roshambo?sslmode=disable"
        )

    return RoshamboConfig(
        dsn=dsn,
        swarm_id=_get(env, "SWARM_ID") or DEFAULT_SWARM_ID,
        embedding_dim=_get_int(env, "EMBEDDING_DIM", DEFAULT_EMBEDDING_DIM),
        lease_ttl_seconds=_get_int(env, "LEASE_TTL_SECONDS", DEFAULT_LEASE_TTL_SECONDS),
        embedding_provider=_get(env, "EMBEDDING_PROVIDER") or DEFAULT_EMBEDDING_PROVIDER,
        aws_region=_get(env, "AWS_REGION") or DEFAULT_AWS_REGION,
        bedrock_model_id=_get(env, "BEDROCK_MODEL_ID") or DEFAULT_BEDROCK_MODEL_ID,
        s3_bucket=_get(env, "S3_BUCKET"),
    )
