"""S3 artifact storage for Roshambo.

Large payloads (long tool outputs, raw LLM responses, big evidence blobs)
should not be stored inline in CockroachDB rows -- ``trails.artifact_uri``
and ``facts`` reference them by ``s3://`` URI instead. This module is the
only place that talks to S3.

Frozen interface (CONTRACT.md): ``put_artifact(cfg, key, data, content_type) -> str``.
``get_artifact`` is the AWS lane's own read-side counterpart (not frozen,
but used by ``worker.py`` and the demo app).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roshambo.config import RoshamboConfig

logger = logging.getLogger("roshambo.aws.s3")

__all__ = ["get_artifact", "parse_s3_uri", "put_artifact"]


class S3NotConfiguredError(RuntimeError):
    """Raised when an S3 operation is attempted without ``cfg.s3_bucket`` set."""


def _client(cfg: RoshamboConfig):
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - exercised via install docs
        raise ImportError(
            "S3 artifact storage requires the 'boto3' package. Install with "
            "the 'aws' extra: pip install 'roshambo[aws]'"
        ) from exc
    return boto3.client("s3", region_name=cfg.aws_region)


def put_artifact(cfg: RoshamboConfig, key: str, data: bytes, content_type: str) -> str:
    """Upload ``data`` to ``s3://{cfg.s3_bucket}/{key}`` and return that URI.

    ``key`` should not include a leading slash. Overwrites any existing
    object at the same key (artifacts are content-identified by whatever
    naming scheme the caller uses, e.g. a trail id or a content hash).
    """
    if not cfg.s3_bucket:
        raise S3NotConfiguredError(
            "cfg.s3_bucket is not set (ROSHAMBO_S3_BUCKET) -- cannot store artifacts"
        )
    key = key.lstrip("/")
    client = _client(cfg)
    client.put_object(
        Bucket=cfg.s3_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    uri = f"s3://{cfg.s3_bucket}/{key}"
    logger.info("wrote artifact %s (%d bytes, %s)", uri, len(data), content_type)
    return uri


def get_artifact(cfg: RoshamboConfig, uri_or_key: str) -> bytes:
    """Read back an artifact. Accepts either a full ``s3://bucket/key`` URI
    (as returned by :func:`put_artifact` and stored in
    ``trails.artifact_uri``) or a bare key resolved against
    ``cfg.s3_bucket``.
    """
    if uri_or_key.startswith("s3://"):
        bucket, key = parse_s3_uri(uri_or_key)
    else:
        if not cfg.s3_bucket:
            raise S3NotConfiguredError(
                "cfg.s3_bucket is not set (ROSHAMBO_S3_BUCKET) -- cannot resolve bare key "
                f"{uri_or_key!r}"
            )
        bucket, key = cfg.s3_bucket, uri_or_key.lstrip("/")
    client = _client(cfg)
    response = client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split ``s3://bucket/key/with/slashes`` into ``(bucket, key)``."""
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3:// URI: {uri!r}")
    without_scheme = uri[len("s3://") :]
    bucket, _, key = without_scheme.partition("/")
    if not bucket or not key:
        raise ValueError(f"malformed s3:// URI (need bucket and key): {uri!r}")
    return bucket, key
