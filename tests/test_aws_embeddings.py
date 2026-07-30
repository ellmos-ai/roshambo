"""Tests for roshambo.embeddings (AWS lane, frozen interface from CONTRACT.md).

Two things are asserted, matching the AWS-lane acceptance criterion verbatim:
"get_embedder() liefert mit gesetzten AWS-Zugangsdaten einen 1024-dimensionalen
Vektor von Bedrock, und ohne Zugangsdaten den klar gekennzeichneten Platzhalter
-- beides durch einen ausgeführten Test belegt."

* The "local" branch (test_local_provider_*) needs no credentials, no mocking,
  and always runs -- this machine genuinely has no AWS credentials configured
  (see docs/EVIDENCE-aws.md), so this is the branch that is actually exercised
  end to end here.
* The "bedrock" branch is exercised two ways:
    - test_bedrock_provider_* mocks boto3 to verify BedrockEmbedder builds the
      request and parses the response correctly (the Titan V2 shape verified
      against AWS's own docs, see module docstring in roshambo/embeddings.py).
      This proves the *code* is correct; it does not prove connectivity to a
      real Bedrock endpoint.
    - test_bedrock_live_* is marked `aws` and skips itself when no AWS
      credentials are present, per CONTRACT.md ground rule 5. It has never
      run against the real service in this environment (no credentials here).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from roshambo.config import RoshamboConfig
from roshambo.embeddings import (
    BedrockEmbedder,
    DeterministicEmbedder,
    get_embedder,
)


def _cfg(**overrides) -> RoshamboConfig:
    defaults = dict(
        dsn="postgresql://unused@localhost:1/none",
        swarm_id="test-swarm",
        embedding_dim=1024,
    )
    defaults.update(overrides)
    return RoshamboConfig(**defaults)


# --------------------------------------------------------------------- local


def test_local_provider_returns_deterministic_embedder():
    embedder = get_embedder(_cfg(embedding_provider="local"))
    assert isinstance(embedder, DeterministicEmbedder)


def test_local_provider_is_flagged_as_a_placeholder():
    """Requested in docs/HANDOFF.md (2026-07-25, core lane): callers that accept
    either lane's placeholder embedder check `is_placeholder` via getattr(...,
    False) to decide whether a recall() result carries real semantic signal.
    Without this flag set, a DeterministicEmbedder-backed result could be
    mistaken for a Bedrock-quality one by that check.
    """
    embedder = get_embedder(_cfg(embedding_provider="local"))
    assert embedder.is_placeholder is True


def test_bedrock_provider_is_not_flagged_as_a_placeholder():
    with patch("boto3.client") as mock_client:
        mock_client.return_value = MagicMock()
        embedder = get_embedder(_cfg(embedding_provider="bedrock"))
    assert getattr(embedder, "is_placeholder", False) is False


def test_local_provider_yields_1024_dim_vector_without_any_aws_credentials():
    assert "AWS_ACCESS_KEY_ID" not in os.environ, (
        "this test is only meaningful when no AWS credentials are configured"
    )
    embedder = get_embedder(_cfg(embedding_provider="local"))
    vector = embedder.embed("no AWS credentials needed for this")
    assert len(vector) == 1024
    assert all(isinstance(component, float) for component in vector)


def test_local_provider_is_deterministic_and_unit_normalized():
    embedder = DeterministicEmbedder(dim=64)
    a = embedder.embed("same text")
    b = embedder.embed("same text")
    assert a == b
    norm_sq = sum(c * c for c in a)
    assert abs(norm_sq - 1.0) < 1e-9


def test_local_provider_logs_itself_clearly_as_a_placeholder(caplog):
    embedder = DeterministicEmbedder(dim=8)
    with caplog.at_level("WARNING", logger="roshambo.embeddings"):
        embedder.embed("check the log")
    assert any("PLACEHOLDER" in record.message for record in caplog.records)
    assert "NOT a semantic model" in DeterministicEmbedder.__doc__


def test_local_provider_embed_batch_matches_embed():
    embedder = DeterministicEmbedder(dim=32)
    texts = ["alpha", "beta", "gamma"]
    assert embedder.embed_batch(texts) == [embedder.embed(t) for t in texts]


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="unknown embedding_provider"):
        get_embedder(_cfg(embedding_provider="carrier-pigeon"))


# ------------------------------------------------------------------- bedrock


def test_bedrock_provider_returns_bedrock_embedder_instance():
    with patch("boto3.client") as mock_client:
        mock_client.return_value = MagicMock()
        embedder = get_embedder(_cfg(embedding_provider="bedrock"))
    assert isinstance(embedder, BedrockEmbedder)
    assert embedder.dim == 1024


def test_bedrock_embed_builds_titan_v2_request_and_parses_response():
    """Verifies the request/response shape against the AWS-documented Titan V2
    contract (inputText/dimensions/normalize in, embedding/inputTextTokenCount
    out) using a mocked bedrock-runtime client -- no network, no credentials.
    """
    fake_body = MagicMock()
    fake_body.read.return_value = (
        b'{"embedding": ' + repr([0.001 * i for i in range(1024)]).encode() + b", "
        b'"inputTextTokenCount": 7, "embeddingsByType": {"float": []}}'
    )
    fake_response = {"body": fake_body}

    with patch("boto3.client") as mock_client_factory:
        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model.return_value = fake_response
        mock_client_factory.return_value = mock_bedrock

        embedder = get_embedder(_cfg(embedding_provider="bedrock"))
        vector = embedder.embed("What are the different services that you offer?")

    assert len(vector) == 1024
    mock_client_factory.assert_called_once_with("bedrock-runtime", region_name="us-east-2")

    call_kwargs = mock_bedrock.invoke_model.call_args.kwargs
    assert call_kwargs["modelId"] == "amazon.titan-embed-text-v2:0"
    import json

    sent_body = json.loads(call_kwargs["body"])
    assert sent_body["inputText"] == "What are the different services that you offer?"
    assert sent_body["dimensions"] == 1024
    assert sent_body["normalize"] is True


def test_bedrock_embed_batch_calls_embed_once_per_text_not_a_batch_endpoint():
    """Titan V2's InvokeModel API has no server-side batch endpoint; embed_batch
    must loop client-side. Also matches the project rule that CockroachDB
    writes always happen one row at a time -- there is never a batch call to
    make on the write path either.
    """
    call_count = {"n": 0}

    def fake_invoke_model(**kwargs):
        call_count["n"] += 1
        body = MagicMock()
        body.read.return_value = b'{"embedding": [0.1, 0.2], "inputTextTokenCount": 1}'
        return {"body": body}

    with patch("boto3.client") as mock_client_factory:
        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model.side_effect = fake_invoke_model
        mock_client_factory.return_value = mock_bedrock

        embedder = get_embedder(_cfg(embedding_provider="bedrock", embedding_dim=2))
        embedder.embed_batch(["one", "two", "three"])

    assert call_count["n"] == 3


def test_bedrock_embed_raises_on_dimension_mismatch():
    fake_body = MagicMock()
    fake_body.read.return_value = b'{"embedding": [0.1, 0.2, 0.3], "inputTextTokenCount": 1}'

    with patch("boto3.client") as mock_client_factory:
        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model.return_value = {"body": fake_body}
        mock_client_factory.return_value = mock_bedrock

        embedder = get_embedder(_cfg(embedding_provider="bedrock", embedding_dim=1024))
        with pytest.raises(ValueError, match="dim"):
            embedder.embed("too short")


@pytest.mark.aws
def test_bedrock_live_returns_1024_dim_vector_from_the_real_service():
    """Requires real AWS credentials with Bedrock model access. Skips cleanly
    otherwise -- see CONTRACT.md ground rule 5. Has not been run against the
    real service in this environment; see docs/EVIDENCE-aws.md.
    """
    if not os.environ.get("AWS_ACCESS_KEY_ID") and not os.environ.get("AWS_PROFILE"):
        pytest.skip("no AWS credentials configured -- see docs/EVIDENCE-aws.md")
    embedder = get_embedder(_cfg(embedding_provider="bedrock"))
    vector = embedder.embed("live Bedrock connectivity check")
    assert len(vector) == 1024
