"""Embedding providers for Roshambo.

Frozen interface (see ``CONTRACT.md``): :class:`Embedder`, :func:`get_embedder`.
Owned by the AWS lane; called by ``roshambo.memory`` (Core lane) to embed trails
and facts before they are written to CockroachDB's ``VECTOR`` columns.

Two implementations:

* :class:`BedrockEmbedder` -- Amazon Titan Text Embeddings V2 on Amazon Bedrock.
  Real semantic embeddings, requires AWS credentials and Bedrock model access.
* :class:`DeterministicEmbedder` -- an **offline placeholder**. It produces a
  vector of the right shape from a hash of the text so the rest of the system
  (schema, lease logic, MCP surface, tests) can run without any AWS
  dependency. It carries **no semantic meaning whatsoever** -- two unrelated
  sentences that happen to hash close together are not "similar" in any
  sense a human would recognize. It exists only so ``get_embedder()`` and
  everything downstream of it keep working without AWS credentials; it must
  never be presented to a user, in a demo, or in documentation as a real
  embedding model.

Research note (2026-07-25): the Titan V2 request/response shape below
(``inputText``/``dimensions``/``normalize``/``embeddingTypes``, defaults
1024/true/float) was verified against the official AWS Bedrock user guide
(model-parameters-titan-embed-text.html) and the AWS SDK code example
(bedrock-runtime_example_bedrock-runtime_InvokeModel_TitanTextEmbeddings),
not assumed. See ``docs/EVIDENCE-aws.md`` for the source URLs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from roshambo.config import RoshamboConfig

logger = logging.getLogger("roshambo.embeddings")

__all__ = [
    "BedrockEmbedder",
    "DeterministicEmbedder",
    "Embedder",
    "get_embedder",
]


@runtime_checkable
class Embedder(Protocol):
    """Frozen interface every embedding provider implements (CONTRACT.md)."""

    dim: int

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]: ...


class BedrockEmbedder:
    """Amazon Titan Text Embeddings V2 via Amazon Bedrock (``bedrock-runtime``).

    Real semantic embeddings. Requires AWS credentials resolvable by boto3's
    default credential chain and access to the configured Bedrock model in
    the configured region (both are account-level grants, not something this
    class can request for itself).

    ``embed_batch`` is provided purely for callers' convenience -- the Titan
    V2 ``InvokeModel`` API embeds exactly one string per call, there is no
    server-side batch endpoint for on-demand inference. It loops over
    :meth:`embed`. This is also consistent with the project rule that rows
    are inserted into CockroachDB one at a time (batch inserts degrade
    vector index quality) -- there is never a reason to want a fused batch
    call on the write path.
    """

    def __init__(self, cfg: RoshamboConfig) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - exercised via install docs
            raise ImportError(
                "BedrockEmbedder requires the 'boto3' package. Install with "
                "the 'aws' extra: pip install 'roshambo[aws]'"
            ) from exc

        self.dim = cfg.embedding_dim
        self._model_id = cfg.bedrock_model_id
        self._client = boto3.client("bedrock-runtime", region_name=cfg.aws_region)

    def embed(self, text: str) -> list[float]:
        body = json.dumps(
            {
                "inputText": text,
                "dimensions": self.dim,
                "normalize": True,
            }
        )
        response = self._client.invoke_model(
            modelId=self._model_id,
            accept="application/json",
            contentType="application/json",
            body=body,
        )
        payload = json.loads(response["body"].read())
        embedding = payload["embedding"]
        if len(embedding) != self.dim:
            raise ValueError(
                f"Bedrock returned a {len(embedding)}-dim embedding, "
                f"configured dimension is {self.dim}"
            )
        return embedding

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class DeterministicEmbedder:
    """OFFLINE PLACEHOLDER embedder. NOT a semantic model. Do not treat its
    output as meaningful similarity -- it exists only so the rest of Roshambo
    runs without AWS credentials (tests, local dev, demos of the *lease*
    logic that don't need real recall quality).

    Produces a deterministic, unit-normalized vector of the configured
    dimension from a SHA-256 hash of the input text. Same text always yields
    the same vector (needed for reproducible tests); different texts yield
    effectively uncorrelated vectors, i.e. this is the semantic opposite of
    an embedding model. It is normalized to unit length purely to match the
    numeric shape (``normalize=True``) of the real Bedrock output, not
    because it carries meaning.

    ``is_placeholder = True`` mirrors ``roshambo.memory.PlaceholderEmbedder`` (Core lane):
    callers that accept either embedder -- ``roshambo.aws.worker``'s ``recall()`` calls,
    ``tests/test_core_recall.py``'s ``test_recall_with_the_real_embedder`` -- check this
    attribute via ``getattr(embedder, "is_placeholder", False)`` to decide whether a
    result carries real semantic signal or must be described as lexical/hash overlap
    only. Requested in ``docs/HANDOFF.md`` (2026-07-25, core lane) and added here so
    that contract holds regardless of which lane's placeholder ends up in use.
    """

    is_placeholder = True

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        logger.warning(
            "DeterministicEmbedder PLACEHOLDER used for embedding (offline, "
            "no AWS credentials) -- this is NOT a semantic model, recall() "
            "results built on it carry no real similarity signal: %r",
            text[:80],
        )
        raw = [self._pseudo_random_component(text, i) for i in range(self.dim)]
        norm = math.sqrt(sum(component * component for component in raw)) or 1.0
        return [component / norm for component in raw]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]

    @staticmethod
    def _pseudo_random_component(text: str, index: int) -> float:
        digest = hashlib.sha256(f"{text}\x00{index}".encode()).digest()
        as_uint64 = int.from_bytes(digest[:8], "big")
        # map to [-1, 1]
        return (as_uint64 / 0xFFFFFFFFFFFFFFFF) * 2.0 - 1.0


def get_embedder(cfg: RoshamboConfig) -> Embedder:
    """Return the embedder configured by ``cfg.embedding_provider``.

    ``"bedrock"`` -> :class:`BedrockEmbedder` (real, requires AWS credentials
    and Bedrock model access). ``"local"`` -> :class:`DeterministicEmbedder`
    (offline placeholder, always available). Callers who need to run without
    AWS credentials (tests, offline dev) select ``"local"`` explicitly via
    ``RoshamboConfig(embedding_provider="local", ...)`` or the
    ``ROSHAMBO_EMBEDDING_PROVIDER=local`` environment variable.
    """
    if cfg.embedding_provider == "bedrock":
        return BedrockEmbedder(cfg)
    if cfg.embedding_provider == "local":
        return DeterministicEmbedder(dim=cfg.embedding_dim)
    raise ValueError(
        f"unknown embedding_provider {cfg.embedding_provider!r}, expected 'bedrock' or 'local'"
    )
