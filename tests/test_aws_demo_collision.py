"""Tests for the two-runtime collision demo (demo/local_agent_worker.py,
demo/run_collision_demo.py).

Most tests here use the same mocking idiom as tests/test_aws_worker.py:
`roshambo.memory.Roshambo` is replaced with a `MagicMock(spec=Roshambo)`, spec'd against the real
class so a wrong method name/signature fails these tests immediately. AWS credentials
are not available in this environment (see docs/EVIDENCE-aws.md), so nothing here
touches boto3/Bedrock for real; these tests prove the demo scripts' own orchestration
logic (registration -> claim -> recall/remember -> release, denial handling, winner
detection, "which side is a real AWS invocation vs a structural dry run") against Core
lane's actual interface.

The one `live`-marked test near the bottom is different: a real CockroachDB v25.4.0
node was reachable in this environment (started by the Core lane for its own
concurrency evidence -- see docs/EVIDENCE-core.md), so `run_local_worker`'s claim/deny
path is also exercised against genuine database state, not a mock. It skips cleanly
when `ROSHAMBO_DSN` is unset. The full two-runtime collision (`run_collision_demo.py`,
both sides at once) was verified by direct script runs against that same live cluster
instead of an automated test -- see docs/EVIDENCE-aws.md for the exact commands and
output; `roshambo.aws.worker.lambda_handler` reads `ROSHAMBO_DSN`/`ROSHAMBO_SWARM_ID` from the
process environment by design (it mirrors how a real Lambda invocation is configured),
which makes it awkward to isolate inside a per-test fixture-provided swarm without
`monkeypatch`-ing the environment, so that side is documented rather than automated
here.
"""

from __future__ import annotations

import dataclasses
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "demo"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import local_agent_worker  # noqa: E402
import run_collision_demo  # noqa: E402

from roshambo.config import RoshamboConfig  # noqa: E402
from roshambo.memory import Roshambo  # noqa: E402
from roshambo.models import Claim, ClaimDenied, RecallHit, Trail  # noqa: E402


def _cfg(**overrides) -> RoshamboConfig:
    defaults = dict(
        dsn="postgresql://unused@localhost:1/none",
        swarm_id="test-swarm",
        embedding_provider="bedrock",  # deliberately NOT "local" -- see the override test
        s3_bucket="roshambo-test-bucket",
    )
    defaults.update(overrides)
    return RoshamboConfig(**defaults)


@pytest.fixture
def mock_roshambo():
    return MagicMock(spec=Roshambo)


# -------------------------------------------------------- local_agent_worker: identity


def test_default_host_label_does_not_contain_the_real_machine_hostname():
    """The demo must never leak the real hostname into public repo data or a recorded
    video -- see the docstring on _default_host_label for why this isn't
    platform.node().
    """
    import platform

    label = local_agent_worker._default_host_label()
    assert label.startswith("local-workstation-")
    assert platform.node() not in label


def test_framework_constant_is_not_aws_branded():
    assert "aws" not in local_agent_worker.FRAMEWORK.lower()
    assert "lambda" not in local_agent_worker.FRAMEWORK.lower()


# --------------------------------------------------- local_agent_worker: success path


def test_run_local_worker_success_flow_and_forces_local_embedding_provider():
    now = datetime.now(timezone.utc)
    claim = Claim(
        claim_id="claim-local-1",
        resource="s3-prefix:bucket/agent-runs/x/",
        agent_id="agent-uuid-1",
        intent="local worker doing the export",
        expires_at=now + timedelta(seconds=300),
    )
    prior_trail = Trail(
        trail_id="trail-0",
        topic="collision demo",
        approach="tried it once before",
        outcome="failure",
        evidence="ran out of time",
        created_at=now - timedelta(days=1),
    )
    mock_roshambo = MagicMock(spec=Roshambo)
    mock_roshambo.register_agent.return_value = "agent-uuid-1"
    mock_roshambo.claim.return_value = claim
    mock_roshambo.recall.return_value = [RecallHit(trail=prior_trail, distance=0.2, strength=1.0)]
    mock_roshambo.remember.return_value = Trail(
        trail_id="trail-local-1",
        topic="t",
        approach="a",
        outcome="success",
        evidence="e",
        created_at=now,
    )
    mock_roshambo.release.return_value = True

    with patch("roshambo.memory.Roshambo", return_value=mock_roshambo) as mock_roshambo_class:
        result = local_agent_worker.run_local_worker(
            _cfg(),
            resource="s3-prefix:bucket/agent-runs/x/",
            intent="local worker doing the export",
            topic="collision demo",
            note="wrote it locally",
            host="fixed-host-for-test",
        )

    # The Roshambo instance backing this worker must be constructed with
    # embedding_provider="local" even though the input cfg said "bedrock" -- this
    # runtime must never depend on AWS credentials being present.
    constructed_cfg = mock_roshambo_class.call_args.args[0]
    assert constructed_cfg.embedding_provider == "local"

    mock_roshambo.register_agent.assert_called_once_with(
        framework="local-cli-agent",
        host="fixed-host-for-test",
        capabilities={"runtime": "local-process", "embedding": "deterministic-placeholder"},
    )
    mock_roshambo.claim.assert_called_once_with(
        resource="s3-prefix:bucket/agent-runs/x/",
        agent_id="agent-uuid-1",
        intent="local worker doing the export",
    )
    mock_roshambo.release.assert_called_once_with("claim-local-1")

    assert result["status"] == "success"
    assert result["framework"] == "local-cli-agent"
    assert result["host"] == "fixed-host-for-test"
    assert result["prior_hits_considered"] == 1
    mock_roshambo.close.assert_called_once()


# ---------------------------------------------------- local_agent_worker: denial path


def test_run_local_worker_denied_writes_an_abandoned_trail_and_never_releases():
    now = datetime.now(timezone.utc)
    denial = ClaimDenied(
        resource="s3-prefix:bucket/agent-runs/x/",
        held_by="some-lambda-agent-id",
        intent="AWS Lambda worker handling this resource",
        expires_at=now + timedelta(seconds=120),
    )
    mock_roshambo = MagicMock(spec=Roshambo)
    mock_roshambo.register_agent.return_value = "agent-uuid-2"
    mock_roshambo.claim.return_value = denial
    mock_roshambo.remember.return_value = Trail(
        trail_id="trail-abandoned-1",
        topic="t",
        approach="a",
        outcome="abandoned",
        evidence="e",
        created_at=now,
    )

    with patch("roshambo.memory.Roshambo", return_value=mock_roshambo):
        result = local_agent_worker.run_local_worker(
            _cfg(),
            resource="s3-prefix:bucket/agent-runs/x/",
            intent="local worker doing the export",
            topic="collision demo",
            note="",
        )

    assert result["status"] == "denied"
    assert result["held_by"] == "some-lambda-agent-id"
    remember_kwargs = mock_roshambo.remember.call_args.kwargs
    assert remember_kwargs["outcome"] == "abandoned"
    assert "some-lambda-agent-id" in remember_kwargs["evidence"]
    mock_roshambo.recall.assert_not_called()
    mock_roshambo.release.assert_not_called()


def test_run_local_worker_waits_on_barrier_before_claiming():
    """`pre_claim_barrier`, if given, must be waited on before claim() -- that is the
    whole mechanism run_collision_demo.py uses to make the two sides' claim() calls
    land close together instead of "whichever thread started first trivially wins".
    """
    now = datetime.now(timezone.utc)
    mock_roshambo = MagicMock(spec=Roshambo)
    mock_roshambo.register_agent.return_value = "agent-uuid-3"
    mock_roshambo.claim.return_value = Claim(
        claim_id="c",
        resource="r",
        agent_id="agent-uuid-3",
        intent="i",
        expires_at=now + timedelta(seconds=60),
    )
    mock_roshambo.recall.return_value = []
    mock_roshambo.remember.return_value = Trail(
        trail_id="t", topic="t", approach="a", outcome="success", evidence="e", created_at=now
    )
    mock_roshambo.release.return_value = True

    call_order: list[str] = []
    mock_barrier = MagicMock()
    mock_barrier.wait.side_effect = lambda: call_order.append("barrier")
    mock_roshambo.claim.side_effect = lambda **kw: (
        call_order.append("claim") or mock_roshambo.claim.return_value
    )

    with patch("roshambo.memory.Roshambo", return_value=mock_roshambo):
        local_agent_worker.run_local_worker(
            _cfg(),
            resource="r",
            intent="i",
            topic="t",
            note="n",
            pre_claim_barrier=mock_barrier,
        )

    assert call_order == ["barrier", "claim"]


# -------------------------------------------------------- run_collision_demo: helpers


def test_default_resource_is_s3_prefix_shaped_not_a_file():
    resource = run_collision_demo._default_resource(_cfg(s3_bucket="my-bucket"))
    assert resource.startswith("s3-prefix:my-bucket/agent-runs/")
    assert resource.endswith("/")
    assert "." not in resource.rsplit("/", 1)[-1]  # no file extension on the leaf segment


def test_invoke_local_simulation_is_labelled_as_not_a_real_invocation():
    canned = {"status": "success", "claim_id": "c1", "trail_id": "t1"}
    with patch("roshambo.aws.worker.lambda_handler", return_value=dict(canned)) as mock_handler:
        result = run_collision_demo._invoke_local_simulation({"resource": "r"})

    mock_handler.assert_called_once_with({"resource": "r"}, None)
    assert result["status"] == "success"
    assert "SIMULATED" in result["invocation"]


def test_invoke_real_lambda_uses_boto3_invoke_and_decodes_payload():
    mock_lambda_client = MagicMock()
    mock_payload = MagicMock()
    mock_payload.read.return_value = b'{"status": "success", "claim_id": "c2"}'
    mock_lambda_client.invoke.return_value = {"Payload": mock_payload}

    with patch("boto3.client", return_value=mock_lambda_client) as mock_client_factory:
        result = run_collision_demo._invoke_real_lambda("roshambo-worker", {"resource": "r"})

    mock_client_factory.assert_called_once_with("lambda")
    mock_lambda_client.invoke.assert_called_once()
    assert result["status"] == "success"
    assert result["invocation"] == "real (boto3 lambda invoke)"


# -------------------------------------------------------- run_collision_demo: winner


def test_run_collision_reports_local_as_winner_when_lambda_side_is_denied():
    def fake_lambda_side(
        *, cfg, mode, resource, topic, intent, task_prompt, function_name, barrier, results
    ):
        barrier.wait()
        results["lambda"] = {"status": "denied", "held_by": "local-cli-agent:some-host"}

    def fake_local_side(*, cfg, resource, topic, intent, note, barrier, results):
        barrier.wait()
        results["local"] = {"status": "success", "claim_id": "c-winner"}

    with (
        patch.object(run_collision_demo, "_run_lambda_side", side_effect=fake_lambda_side),
        patch.object(run_collision_demo, "_run_local_side", side_effect=fake_local_side),
    ):
        summary = run_collision_demo.run_collision(
            _cfg(),
            resource="s3-prefix:b/x/",
            lambda_mode="local-simulate",
            function_name="roshambo-worker",
        )

    assert summary["winner"] == "local"
    assert summary["results"]["lambda"]["status"] == "denied"
    assert summary["results"]["local"]["status"] == "success"


def test_run_lambda_side_catches_its_own_exception_instead_of_crashing_the_thread():
    """The real (unpatched) `_run_lambda_side` must record a {"status": "error", ...}
    entry when something inside it blows up (e.g. no AWS credentials, no deployed
    function) rather than letting the exception escape the thread it runs in -- an
    uncaught exception in a `threading.Thread` target is silently swallowed by the
    interpreter (only printed to stderr), which would leave `results["lambda"]`
    missing entirely and `run_collision()`'s winner detection none the wiser about why.

    A `threading.Barrier(1)` is used instead of a real second thread: with a single
    party, `.wait()` returns immediately, so this exercises the function's own
    register -> barrier -> invoke -> except-and-record logic synchronously.
    """
    import threading

    mock_roshambo = MagicMock(spec=Roshambo)
    mock_roshambo.register_agent.return_value = "lambda-agent-id"
    results: dict = {}

    with (
        patch("roshambo.memory.Roshambo", return_value=mock_roshambo),
        patch.object(
            run_collision_demo,
            "_invoke_local_simulation",
            side_effect=RuntimeError("no AWS credentials configured"),
        ),
    ):
        run_collision_demo._run_lambda_side(
            cfg=_cfg(),
            mode="local-simulate",
            resource="s3-prefix:b/x/",
            topic="t",
            intent="i",
            task_prompt="p",
            function_name="roshambo-worker",
            barrier=threading.Barrier(1),
            results=results,
        )

    assert results["lambda"]["status"] == "error"
    assert "no AWS credentials configured" in results["lambda"]["error"]
    mock_roshambo.close.assert_called_once()


def test_run_lambda_side_local_simulate_success_registers_with_aws_framework():
    import threading

    mock_roshambo = MagicMock(spec=Roshambo)
    mock_roshambo.register_agent.return_value = "lambda-agent-id"
    results: dict = {}
    canned = {"status": "success", "claim_id": "c1", "trail_id": "t1"}

    with (
        patch("roshambo.memory.Roshambo", return_value=mock_roshambo),
        patch.object(run_collision_demo, "_invoke_local_simulation", return_value=dict(canned)),
    ):
        run_collision_demo._run_lambda_side(
            cfg=_cfg(),
            mode="local-simulate",
            resource="s3-prefix:b/x/",
            topic="t",
            intent="i",
            task_prompt="p",
            function_name="roshambo-worker",
            barrier=threading.Barrier(1),
            results=results,
        )

    assert results["lambda"]["status"] == "success"
    assert results["lambda"]["framework"] == run_collision_demo.LAMBDA_FRAMEWORK
    assert results["lambda"]["agent_id"] == "lambda-agent-id"
    register_kwargs = mock_roshambo.register_agent.call_args.kwargs
    assert register_kwargs["framework"] != local_agent_worker.FRAMEWORK


# ------------------------------------------------------------- live: real CockroachDB


@pytest.mark.live
def test_run_local_worker_against_a_real_cluster_claims_then_denies_then_records_it(
    cfg: RoshamboConfig, swarm_id: str
):
    """Against genuine database state (not a mock): a first `run_local_worker` call
    claims the resource, does its work, and releases it -- then, with a second claim
    manually held open, a follow-up call must be denied by the *real* atomic
    `claims` primary key, name the real holder, and still return a well-formed result
    (the abandoned-trail write inside the denial branch must also succeed against the
    real `trails` table).
    """
    from roshambo.memory import Roshambo

    resource = f"live-collision-demo:{swarm_id}"

    first = local_agent_worker.run_local_worker(
        cfg,
        resource=resource,
        intent="first run claims and releases",
        topic="live collision test",
        note="did the work for real, no external call",
    )
    assert first["status"] == "success"
    assert first["released"] is True

    holder = Roshambo(dataclasses.replace(cfg, embedding_provider="local"))
    try:
        held_claim = holder.claim(
            resource=resource, agent_id="live-test-holder", intent="holding it open on purpose"
        )
        assert not hasattr(held_claim, "held_by"), "expected a real Claim, not a ClaimDenied"

        second = local_agent_worker.run_local_worker(
            cfg,
            resource=resource,
            intent="second run should be denied",
            topic="live collision test",
            note="",
        )
        assert second["status"] == "denied"
        assert second["held_by"] == "live-test-holder"
        assert second["intent"] == "holding it open on purpose"
        assert "trail_id" in second  # the abandoned-outcome remember() call succeeded
    finally:
        holder.release(held_claim.claim_id)
        holder.close()
