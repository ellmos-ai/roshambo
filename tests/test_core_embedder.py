"""Tests for the offline placeholder embedder. No database required.

These assert lexical behaviour only. The placeholder is a hashing bag-of-words model,
not a semantic one, and nothing here should be read as evidence about embedding quality.
"""

from __future__ import annotations

import math

import pytest

from roshambo.errors import EmbeddingError
from roshambo.memory import PlaceholderEmbedder


def _cosine_distance(a: list[float], b: list[float]) -> float:
    return 1.0 - sum(x * y for x, y in zip(a, b, strict=True))


def test_dimension_matches_the_schema_default():
    assert PlaceholderEmbedder().dim == 1024
    assert len(PlaceholderEmbedder().embed("anything")) == 1024


def test_vectors_are_unit_length():
    vector = PlaceholderEmbedder(dim=64).embed("connection pool exhausted under load")
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


def test_it_is_deterministic():
    a = PlaceholderEmbedder(dim=64).embed("the same text")
    b = PlaceholderEmbedder(dim=64).embed("the same text")
    assert a == b


def test_empty_text_still_yields_a_usable_vector():
    """Cosine distance against an all-zero vector is undefined, so it must not occur."""
    vector = PlaceholderEmbedder(dim=32).embed("")
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


def test_shared_vocabulary_is_closer_than_unrelated_text():
    embedder = PlaceholderEmbedder(dim=1024)
    anchor = embedder.embed("the deploy failed because the database migration timed out")
    related = embedder.embed("database migration timed out during the deploy")
    unrelated = embedder.embed("sunlight on the harbour wall in late autumn")
    assert _cosine_distance(anchor, related) < _cosine_distance(anchor, unrelated)


def test_batch_matches_single():
    embedder = PlaceholderEmbedder(dim=64)
    texts = ["alpha", "beta"]
    assert embedder.embed_batch(texts) == [embedder.embed(t) for t in texts]


def test_zero_dimension_is_rejected():
    with pytest.raises(EmbeddingError):
        PlaceholderEmbedder(dim=0)


def test_it_announces_itself_as_a_placeholder():
    """Downstream code and docs must be able to tell this apart from a real model."""
    assert PlaceholderEmbedder().is_placeholder is True


def test_placeholder_provider_selects_it_without_touching_the_cloud():
    from roshambo.config import RoshamboConfig
    from roshambo.memory import PLACEHOLDER_PROVIDER, _resolve_embedder

    cfg = RoshamboConfig(
        dsn="postgresql://x@y:1/z",
        swarm_id="s",
        embedding_provider=PLACEHOLDER_PROVIDER,
    )
    assert isinstance(_resolve_embedder(cfg, None), PlaceholderEmbedder)


def test_an_explicit_embedder_always_wins():
    from roshambo.config import RoshamboConfig
    from roshambo.memory import _resolve_embedder

    given = PlaceholderEmbedder(dim=8)
    cfg = RoshamboConfig(dsn="postgresql://x@y:1/z", swarm_id="s")  # provider: bedrock
    assert _resolve_embedder(cfg, given) is given
