"""A worker that plays "some other agent system" for the collision demo.

Roshambo's pitch is not "one Lambda invoked three times" -- it is coordination between
agents that do not know each other: different vendors, different machines, different
sessions. This module is the second, genuinely different half of that story. It:

* imports nothing from ``roshambo.aws`` and never touches boto3/Bedrock/S3. It is not
  "the AWS worker running locally for testing" -- it is meant to *look and behave* like
  a wholly separate, non-AWS agent runtime that happens to coordinate through the same
  Roshambo/CockroachDB layer as ``roshambo.aws.worker.lambda_handler``.
* registers itself via ``Roshambo.register_agent(framework=..., host=...)`` with a
  ``framework`` value distinct from the Lambda worker's, and a synthetic, non-identifying
  ``host`` label (never the real machine hostname -- see ``_default_host_label``) so a
  claim made by this process is visibly attributable to a different system in the demo
  UI and in ``docs/EVIDENCE-aws.md``.
* embeds with ``embedding_provider="local"`` (``DeterministicEmbedder``, see
  ``roshambo.embeddings``) regardless of what ``ROSHAMBO_EMBEDDING_PROVIDER`` says -- this
  process is the "no cloud credentials, no cloud model" side of the demo on purpose.

Run standalone, e.g.:

    python demo/local_agent_worker.py \\
        --resource "s3-prefix:roshambo-demo-bucket/agent-runs/2026-07-25/" \\
        --topic "write the nightly export" \\
        --intent "write the nightly export directly from a local batch job" \\
        --note "wrote export.csv to the prefix from a local disk buffer"

or import :func:`run_local_worker` directly -- that is what
``demo/run_collision_demo.py`` does to race this against the Lambda worker.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# This runtime's identity in the `agents` table (schema/001_init.sql: `framework STRING
# NOT NULL -- claude | codex | gemini | kimi | lambda | ...`). Deliberately unlike
# anything AWS-branded -- this process's whole point is to be a stand-in for "some other
# agent framework", not a specific real product this repository is not actually
# integrating with.
FRAMEWORK = "local-cli-agent"


def _default_host_label() -> str:
    """A synthetic, non-identifying stand-in for "which machine this is".

    Deliberately not ``platform.node()``/``socket.gethostname()``: the real hostname of
    whatever machine runs this demo is not something that belongs in a public
    repository's example data or in a recorded pitch video. ``--host`` overrides this
    with anything more narratively useful (e.g. "on-prem-batch-node-3").
    """
    return f"local-workstation-{uuid.uuid4().hex[:6]}"


def run_local_worker(
    cfg: Any,
    *,
    resource: str,
    intent: str,
    topic: str,
    note: str,
    host: str | None = None,
    ttl_seconds: int | None = None,
    pre_claim_barrier: Any | None = None,
) -> dict:
    """Register, claim, (maybe) work, remember, release. Returns a JSON-shaped dict in
    the same spirit as ``roshambo.aws.worker.lambda_handler``'s response, so the two
    workers' output is directly comparable in the collision demo's printed summary --
    but this is this module's own convention, not part of CONTRACT.md's frozen shape.
    """
    from roshambo.memory import Roshambo

    # This process never uses Bedrock: force the local placeholder embedder regardless
    # of what ROSHAMBO_EMBEDDING_PROVIDER says, so a run of this script can never silently
    # depend on AWS credentials being present.
    local_cfg = dataclasses.replace(cfg, embedding_provider="local")
    roshambo = Roshambo(local_cfg)
    try:
        host_label = host or _default_host_label()
        agent_id = roshambo.register_agent(
            framework=FRAMEWORK,
            host=host_label,
            capabilities={"runtime": "local-process", "embedding": "deterministic-placeholder"},
        )

        claim_kwargs: dict[str, Any] = {
            "resource": resource,
            "agent_id": agent_id,
            "intent": intent,
        }
        if ttl_seconds is not None:
            claim_kwargs["ttl_seconds"] = ttl_seconds

        if pre_claim_barrier is not None:
            # Used by demo/run_collision_demo.py: hold here until the other side has
            # also finished its own registration/setup, so the two claim() calls that
            # actually contend for the resource land as close together as two Python
            # threads can manage, instead of "whichever thread happened to start first
            # trivially wins because the other side was still doing setup work".
            pre_claim_barrier.wait()
        claim_result = roshambo.claim(**claim_kwargs)

        if hasattr(claim_result, "held_by"):  # ClaimDenied -- see worker.py for why this
            # duck-typed check instead of an isinstance import from roshambo.models
            denial = claim_result
            trail = roshambo.remember(
                topic=topic,
                approach=intent,
                outcome="abandoned",
                evidence=(
                    f"lost the race for {resource}: held by {denial.held_by} "
                    f"({denial.intent}), expires {denial.expires_at.isoformat()}"
                ),
                agent_id=agent_id,
            )
            return {
                "status": "denied",
                "framework": FRAMEWORK,
                "host": host_label,
                "agent_id": agent_id,
                "resource": denial.resource,
                "held_by": denial.held_by,
                "intent": denial.intent,
                "expires_at": denial.expires_at.isoformat(),
                "trail_id": trail.trail_id,
            }

        claim = claim_result
        prior_hits = roshambo.recall(query=topic, limit=5)

        evidence = note or (
            f"claimed {resource} and completed the work locally, no external call made"
        )
        trail = roshambo.remember(
            topic=topic,
            approach=intent,
            outcome="success",
            evidence=evidence,
            agent_id=agent_id,
            detail={"prior_hits": len(prior_hits), "runtime": "local-process"},
        )
        released = roshambo.release(claim.claim_id)

        return {
            "status": "success",
            "framework": FRAMEWORK,
            "host": host_label,
            "agent_id": agent_id,
            "claim_id": claim.claim_id,
            "trail_id": trail.trail_id,
            "outcome": "success",
            "evidence": evidence,
            "prior_hits_considered": len(prior_hits),
            "released": released,
        }
    finally:
        roshambo.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--resource", required=True, help='e.g. "s3-prefix:bucket/prefix/"')
    parser.add_argument("--topic", required=True, help="short topic, used for recall()/remember()")
    parser.add_argument(
        "--intent", required=True, help="human-readable: what this run intends to do"
    )
    parser.add_argument("--note", default="", help="what this run actually did (the evidence text)")
    parser.add_argument("--host", default=None, help="override the synthetic host label")
    parser.add_argument("--ttl", type=int, default=None, help="lease TTL override, seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    from roshambo.config import load_config

    args = _build_parser().parse_args(argv)
    try:
        cfg = load_config()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    result = run_local_worker(
        cfg,
        resource=args.resource,
        intent=args.intent,
        topic=args.topic,
        note=args.note,
        host=args.host,
        ttl_seconds=args.ttl,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
