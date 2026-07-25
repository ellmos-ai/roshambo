"""Tests for roshambo.aws.worker.lambda_handler.

No live CockroachDB cluster and no AWS credentials are available in this
environment (verified, see docs/EVIDENCE-aws.md) -- there is no Docker
either, so Core lane's own `live`-marked tests against a real cluster cannot
run here either. To still exercise the full claim -> recall -> work ->
remember -> release control flow end to end, `roshambo.memory.Roshambo` is
replaced with a `unittest.mock.Mock(spec=Roshambo)` -- spec'd against the real,
already-delivered `Roshambo` class, so a typo'd method name or wrong signature
in worker.py would fail these tests immediately rather than being silently
accepted by a hand-rolled stub. Return values are real dataclasses from
`roshambo.models` (Claim, ClaimDenied, Trail, RecallHit), not fakes. Only the
network-touching pieces (the DB connection inside Roshambo, and the Bedrock
Claude call) are mocked.

This is an honest middle ground: it proves worker.py's orchestration logic
is correct against Core lane's actual interface, but it is not evidence that
a real CockroachDB cluster or a real Bedrock endpoint behaves as assumed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from roshambo.aws import worker as worker_module
from roshambo.config import RoshamboConfig
from roshambo.memory import Roshambo
from roshambo.models import Claim, ClaimDenied, RecallHit, Trail


def _cfg() -> RoshamboConfig:
    return RoshamboConfig(
        dsn="postgresql://unused@localhost:1/none",
        swarm_id="test-swarm",
        embedding_provider="local",
        s3_bucket="roshambo-test-bucket",
    )


def _example_event(**overrides) -> dict:
    event = {
        "resource": "task:example",
        "agent_id": "lambda-worker-1",
        "intent": "run the example task",
        "topic": "example task",
        "task_prompt": "Do the example task.",
    }
    event.update(overrides)
    return event


def _mock_bedrock_converse(response_text: str):
    """Patch boto3.client so worker._invoke_claude gets a canned Converse response."""
    mock_bedrock = MagicMock()
    mock_bedrock.converse.return_value = {
        "output": {"message": {"content": [{"text": response_text}]}}
    }
    return mock_bedrock


@pytest.fixture
def mock_roshambo():
    """A Mock spec'd against the real Roshambo class from Core lane."""
    return MagicMock(spec=Roshambo)


def _patched(mock_roshambo, mock_bedrock):
    """Context manager stack patching load_config/Roshambo/boto3 for one call."""
    return (
        patch("roshambo.config.load_config", return_value=_cfg()),
        patch("roshambo.memory.Roshambo", return_value=mock_roshambo),
        patch("boto3.client", return_value=mock_bedrock),
    )


def test_missing_required_fields_raises_before_touching_roshambo():
    with pytest.raises(worker_module.WorkerConfigError, match="resource"):
        worker_module.lambda_handler({"agent_id": "a"}, None)


def test_full_flow_claim_recall_work_remember_release():
    now = datetime.now(timezone.utc)
    claim = Claim(
        claim_id="claim-1",
        resource="task:example",
        agent_id="lambda-worker-1",
        intent="run the example task",
        expires_at=now + timedelta(seconds=300),
    )
    prior_trail = Trail(
        trail_id="trail-0",
        topic="example task",
        approach="tried the naive approach",
        outcome="failure",
        evidence="timed out after 30s",
        created_at=now - timedelta(days=1),
    )
    recall_hits = [RecallHit(trail=prior_trail, distance=0.12, strength=1.0)]
    remembered_trail = Trail(
        trail_id="trail-1",
        topic="example task",
        approach="Do the example task.",
        outcome="success",
        evidence="Hello, I am Claude on Bedrock.",
        created_at=now,
        agent_id="lambda-worker-1",
    )

    mock_roshambo = MagicMock(spec=Roshambo)
    mock_roshambo.claim.return_value = claim
    mock_roshambo.recall.return_value = recall_hits
    mock_roshambo.remember.return_value = remembered_trail
    mock_roshambo.release.return_value = True

    mock_bedrock = _mock_bedrock_converse("Hello, I am Claude on Bedrock.")

    p1, p2, p3 = _patched(mock_roshambo, mock_bedrock)
    with p1, p2, p3:
        result = worker_module.lambda_handler(_example_event(), None)

    assert result["status"] == "success"
    assert result["outcome"] == "success"
    assert result["claim_id"] == "claim-1"
    assert result["trail_id"] == "trail-1"
    assert result["prior_hits_considered"] == 1

    mock_roshambo.claim.assert_called_once_with(
        resource="task:example", agent_id="lambda-worker-1", intent="run the example task"
    )
    mock_roshambo.recall.assert_called_once_with(query="example task", limit=5)
    mock_roshambo.remember.assert_called_once()
    remember_kwargs = mock_roshambo.remember.call_args.kwargs
    assert remember_kwargs["outcome"] == "success"
    assert remember_kwargs["topic"] == "example task"
    assert "Hello, I am Claude on Bedrock." in remember_kwargs["evidence"]
    mock_roshambo.release.assert_called_once_with("claim-1")

    # recall happened before remember, and release happened before remember
    # returns (release-before-finally-then-remember is the documented order)
    call_order = [c[0] for c in mock_roshambo.method_calls]
    assert call_order.index("claim") < call_order.index("recall")
    assert call_order.index("recall") < call_order.index("release")
    assert call_order.index("release") < call_order.index("remember")


def test_claim_denied_short_circuits_before_recall_or_remember():
    now = datetime.now(timezone.utc)
    denial = ClaimDenied(
        resource="task:example",
        held_by="some-other-agent",
        intent="already working on this",
        expires_at=now + timedelta(seconds=120),
    )
    mock_roshambo = MagicMock(spec=Roshambo)
    mock_roshambo.claim.return_value = denial
    mock_bedrock = _mock_bedrock_converse("should never be called")

    p1, p2, p3 = _patched(mock_roshambo, mock_bedrock)
    with p1, p2, p3:
        result = worker_module.lambda_handler(_example_event(), None)

    assert result["status"] == "denied"
    assert result["held_by"] == "some-other-agent"
    assert result["intent"] == "already working on this"
    mock_roshambo.recall.assert_not_called()
    mock_roshambo.remember.assert_not_called()
    mock_roshambo.release.assert_not_called()
    mock_bedrock.converse.assert_not_called()


def test_work_step_exception_is_recorded_as_failure_and_still_releases():
    now = datetime.now(timezone.utc)
    claim = Claim(
        claim_id="claim-2",
        resource="task:example",
        agent_id="lambda-worker-1",
        intent="run the example task",
        expires_at=now + timedelta(seconds=300),
    )
    mock_roshambo = MagicMock(spec=Roshambo)
    mock_roshambo.claim.return_value = claim
    mock_roshambo.recall.return_value = []
    mock_roshambo.remember.return_value = Trail(
        trail_id="trail-2",
        topic="example task",
        approach="Do the example task.",
        outcome="failure",
        evidence="boom",
        created_at=now,
    )
    mock_roshambo.release.return_value = True

    mock_bedrock = MagicMock()
    mock_bedrock.converse.side_effect = RuntimeError("boom")

    p1, p2, p3 = _patched(mock_roshambo, mock_bedrock)
    with p1, p2, p3:
        result = worker_module.lambda_handler(_example_event(), None)

    assert result["outcome"] == "failure"
    assert "boom" in result["evidence"]
    mock_roshambo.release.assert_called_once_with("claim-2")  # released even though work failed
    remember_kwargs = mock_roshambo.remember.call_args.kwargs
    assert remember_kwargs["outcome"] == "failure"


def test_large_response_is_written_to_s3_and_evidence_is_truncated():
    now = datetime.now(timezone.utc)
    claim = Claim(
        claim_id="claim-3",
        resource="task:example",
        agent_id="lambda-worker-1",
        intent="run the example task",
        expires_at=now + timedelta(seconds=300),
    )
    mock_roshambo = MagicMock(spec=Roshambo)
    mock_roshambo.claim.return_value = claim
    mock_roshambo.recall.return_value = []
    mock_roshambo.remember.return_value = Trail(
        trail_id="trail-3",
        topic="example task",
        approach="Do the example task.",
        outcome="success",
        evidence="(long)",
        created_at=now,
    )
    mock_roshambo.release.return_value = True

    long_text = "x" * (worker_module.LARGE_OUTPUT_THRESHOLD_CHARS + 500)
    mock_bedrock = _mock_bedrock_converse(long_text)

    p1, p2, p3 = _patched(mock_roshambo, mock_bedrock)
    with p1, p2, p3, patch(
        "roshambo.aws.worker.put_artifact", return_value="s3://roshambo-test-bucket/worker-outputs/x.txt"
    ) as mock_put:
        result = worker_module.lambda_handler(_example_event(), None)

    mock_put.assert_called_once()
    assert result["artifact_uri"] == "s3://roshambo-test-bucket/worker-outputs/x.txt"
    remember_kwargs = mock_roshambo.remember.call_args.kwargs
    assert remember_kwargs["artifact_uri"] == "s3://roshambo-test-bucket/worker-outputs/x.txt"
    assert len(remember_kwargs["evidence"]) < len(long_text)
    assert "truncated" in remember_kwargs["evidence"]
