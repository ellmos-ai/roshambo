"""One command for the pitch video's "beat 1": two different agent runtimes collide.

Roshambo is a coordinator for agents that do not know each other -- different vendors,
different machines, different sessions -- not a memory store for one agent framework
talking to itself. Showing that with three copies of the same Lambda would only prove
"CockroachDB can lock a row three times." This script instead races exactly two
*different* runtimes for the *same* resource:

* the AWS side: ``roshambo.aws.worker.lambda_handler`` -- either a real, already-deployed
  ``roshambo-worker`` Lambda invoked over the network (``--lambda-mode invoke``), or the
  same handler called in-process for a structural dry run (``--lambda-mode
  local-simulate``, the default, since this build environment has no AWS credentials
  and no deployed function -- see docs/EVIDENCE-aws.md). ``local-simulate`` still runs
  on this machine and does **not** demonstrate "a different machine"; only ``invoke``
  does. The script prints which mode it ran in so this is never ambiguous after the
  fact.
* the non-AWS side: ``demo.local_agent_worker.run_local_worker`` -- a separate code path
  that never imports boto3, registered under a different ``framework``/``host`` in the
  ``agents`` table (see ``schema/001_init.sql``).

The contested resource defaults to an S3-*prefix*-shaped string (``s3-prefix:<bucket>/
agent-runs/<run-id>/``) rather than a filename -- Roshambo coordinates arbitrary named
resources, and a prefix under an S3 bucket is a concrete example of "not a file". No
actual S3 object is written by this script; the prefix is a coordination key both sides
must respect before *they* would write into it, which is the point being demonstrated
(claim before you touch a shared cloud location), not object storage itself.

Whichever side loses gets a ``ClaimDenied`` naming the winner and its intent, and (via
both workers' own code) writes an ``outcome="abandoned"`` trail recording that -- so the
collision is not just something printed to a terminal, it is itself a piece of Roshambo's
memory that a later ``recall()`` can surface.

Requires a live CockroachDB cluster (``ROSHAMBO_DSN``); with ``--lambda-mode invoke`` it
also requires AWS credentials and an already-deployed ``roshambo-worker`` function (see
``infra/deploy_lambda.py``). Has not been run against either in this build environment
-- see docs/EVIDENCE-aws.md for exactly what was and wasn't exercised.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `import local_agent_worker`
from local_agent_worker import run_local_worker  # noqa: E402

LAMBDA_FRAMEWORK = "aws-lambda-bedrock"


def _default_resource(cfg: Any) -> str:
    bucket = cfg.s3_bucket or "roshambo-demo-bucket-not-configured"
    run_id = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())
    return f"s3-prefix:{bucket}/agent-runs/{run_id}/"


def _run_lambda_side(
    cfg: Any,
    *,
    mode: str,
    resource: str,
    topic: str,
    intent: str,
    task_prompt: str,
    function_name: str,
    barrier: threading.Barrier,
    results: dict,
) -> None:
    from roshambo.memory import Roshambo

    roshambo = Roshambo(cfg)
    try:
        agent_id = roshambo.register_agent(
            framework=LAMBDA_FRAMEWORK,
            host=f"aws-lambda:{cfg.aws_region}:{function_name}",
            capabilities={"runtime": "aws-lambda", "model": cfg.bedrock_model_id},
        )
        event = {
            "resource": resource,
            "agent_id": agent_id,
            "intent": intent,
            "topic": topic,
            "task_prompt": task_prompt,
        }

        barrier.wait()

        if mode == "invoke":
            results["lambda"] = _invoke_real_lambda(function_name, event)
        else:
            results["lambda"] = _invoke_local_simulation(event)
        results["lambda"]["agent_id"] = agent_id
        results["lambda"]["framework"] = LAMBDA_FRAMEWORK
    except Exception as exc:  # noqa: BLE001 - report, don't crash the other thread
        results["lambda"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        roshambo.close()


def _invoke_real_lambda(function_name: str, event: dict) -> dict:
    import boto3

    lam = boto3.client("lambda")
    response = lam.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(event).encode("utf-8"),
    )
    payload = json.loads(response["Payload"].read())
    payload["invocation"] = "real (boto3 lambda invoke)"
    if response.get("FunctionError"):
        payload["function_error"] = response["FunctionError"]
    return payload


def _invoke_local_simulation(event: dict) -> dict:
    from roshambo.aws.worker import lambda_handler

    payload = lambda_handler(event, None)
    payload["invocation"] = (
        "SIMULATED -- lambda_handler() called in-process, not a real AWS Lambda "
        "invocation. Does not demonstrate a second machine; structural dry run only."
    )
    return payload


def _run_local_side(
    cfg: Any,
    *,
    resource: str,
    topic: str,
    intent: str,
    note: str,
    barrier: threading.Barrier,
    results: dict,
) -> None:
    try:
        results["local"] = run_local_worker(
            cfg,
            resource=resource,
            intent=intent,
            topic=topic,
            note=note,
            pre_claim_barrier=barrier,
        )
    except Exception as exc:  # noqa: BLE001
        results["local"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def run_collision(
    cfg: Any,
    *,
    resource: str | None,
    lambda_mode: str,
    function_name: str,
) -> dict:
    resource = resource or _default_resource(cfg)
    topic = "collision demo: contested resource"
    run_tag = uuid.uuid4().hex[:8]

    barrier = threading.Barrier(2)
    results: dict[str, dict] = {}

    lambda_thread = threading.Thread(
        target=_run_lambda_side,
        kwargs=dict(
            cfg=cfg,
            mode=lambda_mode,
            resource=resource,
            topic=topic,
            intent=f"[run {run_tag}] AWS Lambda worker handling this resource",
            task_prompt="Say which resource you claimed and confirm you are done.",
            function_name=function_name,
            barrier=barrier,
            results=results,
        ),
    )
    local_thread = threading.Thread(
        target=_run_local_side,
        kwargs=dict(
            cfg=cfg,
            resource=resource,
            topic=topic,
            intent=f"[run {run_tag}] local-cli-agent worker handling this resource",
            note=f"[run {run_tag}] wrote the export locally, no external model call",
            barrier=barrier,
            results=results,
        ),
    )

    lambda_thread.start()
    local_thread.start()
    lambda_thread.join()
    local_thread.join()

    winner = None
    for side, result in results.items():
        if result.get("status") == "success":
            winner = side
    return {"resource": resource, "lambda_mode": lambda_mode, "winner": winner, "results": results}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--resource", default=None, help="defaults to a generated S3-prefix string")
    parser.add_argument(
        "--lambda-mode",
        choices=["invoke", "local-simulate"],
        default="local-simulate",
        help="'invoke' calls a real deployed Lambda over the network (needs AWS creds "
        "and infra/deploy_lambda.py deploy having been run); 'local-simulate' (default) "
        "calls lambda_handler() in-process as a structural dry run only",
    )
    parser.add_argument("--function-name", default="roshambo-worker")
    return parser


def main(argv: list[str] | None = None) -> int:
    from roshambo.config import load_config

    args = _build_parser().parse_args(argv)
    try:
        cfg = load_config()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    summary = run_collision(
        cfg, resource=args.resource, lambda_mode=args.lambda_mode, function_name=args.function_name
    )
    print(json.dumps(summary, indent=2, default=str))

    if args.lambda_mode == "local-simulate":
        print(
            "\nNOTE: --lambda-mode local-simulate ran the Lambda handler in-process on "
            "this machine. It exercises the same code path but does NOT demonstrate two "
            "machines. For the real two-machine collision: deploy with "
            "infra/deploy_lambda.py (package, create-role, deploy), then re-run with "
            "--lambda-mode invoke.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
