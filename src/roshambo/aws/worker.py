"""``roshambo-worker`` -- the autonomous Lambda worker.

This is the "agent that spawns itself" side of the demo: a Lambda function
that, on invocation, tries to claim a resource, checks Roshambo's memory for
prior attempts, does a unit of work (a Bedrock call to Claude in the demo),
and writes back what happened -- success or failure -- before releasing its
claim. It is meant to be invoked concurrently (e.g. three Lambdas racing for
the same ``resource``) to demonstrate that CockroachDB's serializable lease
gives exactly one winner.

Frozen interface (CONTRACT.md): ``lambda_handler(event, context) -> dict``.

Event shape (this module's own convention, not frozen by CONTRACT.md):

    {
        "resource": "task:example-resource",   # required, what to claim
        "agent_id": "lambda-worker-1",          # required, this worker's identity
        "intent": "human-readable, what I plan to do",  # required
        "topic": "short topic used for recall() and remember()",  # required
        "task_prompt": "the actual instruction handed to Claude",  # required
        "ttl_seconds": 300                      # optional, lease TTL override
    }

Response shape:

    {"status": "denied", "resource": ..., "held_by": ..., "intent": ..., "expires_at": ...}
    {"status": "success", "claim_id": ..., "trail_id": ..., "outcome": "success", "evidence": ...}
    {"status": "success", "claim_id": ..., "trail_id": ..., "outcome": "failure", "evidence": ...}

A denial is not an error -- CONTRACT.md is explicit that ``ClaimDenied`` is a
product feature, not a failure path. This handler treats it the same way:
status "denied" with a 200-shaped dict, not a raised exception, so a Lambda
orchestrating three simultaneous workers sees two clean denials rather than
two stack traces.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

from roshambo.aws.s3 import put_artifact
from roshambo.embeddings import get_embedder

logger = logging.getLogger("roshambo.aws.worker")
if not logger.handlers:
    logging.basicConfig(level=os.environ.get("ROSHAMBO_LOG_LEVEL", "INFO"))

# Response text above this size goes to S3; only the s3:// URI is written to
# the trails table. Keeps CockroachDB rows small and keeps large blobs out of
# the vector-indexed evidence column. Chosen generously for a demo; tune per
# deployment.
LARGE_OUTPUT_THRESHOLD_CHARS = 4000

# Model used for the worker's "do the work" step. Overridable per deployment
# via ROSHAMBO_WORKER_BEDROCK_MODEL_ID. The default below is a Claude model ID
# verified present in AWS's own Converse API documentation as of the 2026-07-25
# research pass (see docs/EVIDENCE-aws.md) -- it is NOT necessarily the most
# capable or most current Claude model available on Bedrock in your account
# and region. Model catalogs change; before a real run, check
# `aws bedrock list-foundation-models --by-provider anthropic` and override
# this env var rather than trusting the hardcoded default.
DEFAULT_WORKER_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"


class WorkerConfigError(RuntimeError):
    """Raised when the incoming Lambda event is missing required fields."""


def lambda_handler(event: dict, context: Any) -> dict:
    """Entry point Lambda invokes. See module docstring for event/response shape."""
    # Imported lazily (not at module top level) so that `roshambo.aws.worker` --
    # and therefore `roshambo.aws`, which re-exports lambda_handler -- stays
    # importable even before roshambo.config/roshambo.memory (Core lane) exist.
    # Also keeps Lambda cold starts that never invoke the handler cheap.
    from roshambo.config import load_config
    from roshambo.memory import Roshambo

    _require_fields(event, ["resource", "agent_id", "intent", "topic", "task_prompt"])

    cfg = load_config()
    embedder = get_embedder(cfg)
    roshambo = Roshambo(cfg, embedder=embedder)

    resource = event["resource"]
    agent_id = event["agent_id"]
    intent = event["intent"]
    topic = event["topic"]
    task_prompt = event["task_prompt"]
    ttl_seconds = event.get("ttl_seconds")

    claim_kwargs = {"resource": resource, "agent_id": agent_id, "intent": intent}
    if ttl_seconds is not None:
        claim_kwargs["ttl_seconds"] = ttl_seconds
    claim_result = roshambo.claim(**claim_kwargs)

    # ClaimDenied and Claim are distinguished by field, not by importing
    # roshambo.models here -- keeps this module's import graph independent of
    # roshambo.memory's internal type module while the two lanes build in
    # parallel. Field names are frozen by CONTRACT.md.
    if hasattr(claim_result, "held_by"):
        logger.info(
            "claim denied for resource=%s: held by %s (%s), expires %s -- "
            "this is expected coordination, not an error",
            resource,
            claim_result.held_by,
            claim_result.intent,
            claim_result.expires_at,
        )
        return {
            "status": "denied",
            "resource": claim_result.resource,
            "held_by": claim_result.held_by,
            "intent": claim_result.intent,
            "expires_at": claim_result.expires_at.isoformat(),
        }

    claim = claim_result
    logger.info("claimed resource=%s claim_id=%s agent_id=%s", resource, claim.claim_id, agent_id)

    try:
        prior_hits = roshambo.recall(query=topic, limit=5)
    except Exception:
        logger.exception("recall() failed, proceeding with empty prior context")
        prior_hits = []

    try:
        outcome, evidence, detail, artifact_uri = _do_work(
            cfg, agent_id=agent_id, task_prompt=task_prompt, prior_hits=prior_hits
        )
    except Exception as exc:  # the work step itself failed -- record that, don't just crash
        logger.exception("work step failed for resource=%s", resource)
        outcome, evidence, detail, artifact_uri = "failure", f"unhandled exception: {exc}", {}, None
    finally:
        released = roshambo.release(claim.claim_id)
        logger.info("released claim_id=%s ok=%s", claim.claim_id, released)

    trail = roshambo.remember(
        topic=topic,
        approach=task_prompt,
        outcome=outcome,
        evidence=evidence,
        agent_id=agent_id,
        detail=detail,
        artifact_uri=artifact_uri,
    )

    return {
        "status": "success",
        "claim_id": claim.claim_id,
        "trail_id": trail.trail_id,
        "outcome": outcome,
        "evidence": evidence,
        "artifact_uri": artifact_uri,
        "prior_hits_considered": len(prior_hits),
    }


def _do_work(
    cfg,
    *,
    agent_id: str,
    task_prompt: str,
    prior_hits: list,
) -> tuple[str, str, dict, str | None]:
    """Run the unit of work: one Bedrock Converse call to Claude, informed by
    prior trails. Returns (outcome, evidence, detail, artifact_uri).

    outcome is one of Roshambo's Outcome literals: "success" | "failure" |
    "inconclusive" (this worker never returns "abandoned" -- that is a
    human/orchestrator judgement call, not something a single Lambda
    invocation decides about itself).
    """
    context_lines = [
        f"- [{hit.trail.outcome}] {hit.trail.approach} -> {hit.trail.evidence} "
        f"(distance={hit.distance:.4f})"
        for hit in prior_hits
    ]
    context_block = (
        "\n".join(context_lines) if context_lines else "(no prior attempts found)"
    )
    full_prompt = (
        f"{task_prompt}\n\n"
        f"Prior attempts recalled from Roshambo's memory for this topic:\n{context_block}\n\n"
        "Take prior failures into account. State clearly whether you succeeded, "
        "and if not, why."
    )

    response_text = _invoke_claude(cfg, full_prompt)

    detail: dict = {"prior_hits": len(prior_hits)}
    artifact_uri = None
    evidence = response_text

    if len(response_text) > LARGE_OUTPUT_THRESHOLD_CHARS:
        key = f"worker-outputs/{agent_id}/{uuid.uuid4()}.txt"
        artifact_uri = put_artifact(
            cfg, key, response_text.encode("utf-8"), content_type="text/plain; charset=utf-8"
        )
        evidence = response_text[:LARGE_OUTPUT_THRESHOLD_CHARS] + " [truncated, see artifact_uri]"
        detail["full_response_bytes"] = len(response_text.encode("utf-8"))

    # Simple, honest heuristic for the demo: the worker itself doesn't run
    # arbitrary code, so "success" means Claude produced a non-empty answer
    # without the call raising. A real deployment would replace this with
    # whatever domain-specific check defines "did the task actually work".
    outcome = "success" if response_text.strip() else "inconclusive"
    return outcome, evidence, detail, artifact_uri


def _invoke_claude(cfg, prompt: str) -> str:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - exercised via install docs
        raise ImportError(
            "The worker's Bedrock Claude call requires the 'boto3' package. "
            "Install with the 'aws' extra: pip install 'roshambo[aws]'"
        ) from exc

    model_id = os.environ.get("ROSHAMBO_WORKER_BEDROCK_MODEL_ID", DEFAULT_WORKER_MODEL_ID)
    client = boto3.client("bedrock-runtime", region_name=cfg.aws_region)
    response = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 1024, "temperature": 0.2},
    )
    blocks = response["output"]["message"]["content"]
    return "".join(block.get("text", "") for block in blocks)


def _require_fields(event: dict, fields: list[str]) -> None:
    missing = [f for f in fields if not event.get(f)]
    if missing:
        raise WorkerConfigError(f"event is missing required field(s): {', '.join(missing)}")


def _local_invocation_id() -> str:
    """Small helper for manual/CLI test invocations outside real Lambda."""
    return f"local-{int(time.time())}-{uuid.uuid4().hex[:8]}"


if __name__ == "__main__":  # pragma: no cover - manual smoke-test entry point
    example_event = {
        "resource": "demo:example-task",
        "agent_id": _local_invocation_id(),
        "intent": "manual smoke test from worker.py __main__",
        "topic": "manual smoke test",
        "task_prompt": "Say hello and confirm you are Claude on Bedrock.",
    }
    print(json.dumps(lambda_handler(example_event, None), indent=2, default=str))
