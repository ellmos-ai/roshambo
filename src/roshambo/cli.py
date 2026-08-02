"""Command line interface.

Deliberately narrow: the checked verbs the agent-facing API exposes, plus schema
setup and a status read. There is no "run arbitrary SQL" subcommand — if you want that,
use the CockroachDB Managed MCP server or `cockroach sql`, which is the read path.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .config import load_config
from .db import apply_schema
from .errors import RoshamboError
from .memory import Roshambo
from .models import Claim


def _build_parser() -> argparse.ArgumentParser:
    # `--json` is accepted both before the subcommand (`roshambo --json status`, the
    # only form that worked before this fix) and after it (`roshambo status --json`,
    # matching every subcommand's own flags and the form people actually type first).
    # Plain argparse gives a top-level optional to the top-level parser only, so
    # `status --json` failed with "unrecognized arguments: --json" and exit code 2 --
    # the same flag-position trap already documented for `--resource` on
    # `heartbeat`/`release` in docs/NEXT-RUN.md, just unnoticed here because no test
    # exercised `--json` at all.
    #
    # The naive fix -- give every subparser the same `--json` via a shared `parents=`
    # parent -- reintroduces a *different* bug: `argparse.ArgumentParser.parse_args`
    # always merges a subparser's own namespace over the top-level one
    # (`_SubParsersAction.__call__`), so a subparser's default (False) silently
    # clobbers a `--json` already parsed before the subcommand, e.g.
    # `roshambo --json status` would parse but come back `json=False`. Giving the
    # subparser copy `default=SUPPRESS` avoids that: when `--json` is absent from the
    # subcommand's own arguments, argparse never adds a `json` key to the subparser's
    # namespace at all, so there is nothing to overwrite the top-level value with.
    json_parent = argparse.ArgumentParser(add_help=False)
    json_parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="machine-readable output where applicable",
    )

    parser = argparse.ArgumentParser(
        prog="roshambo",
        description=(
            "Agentic memory on CockroachDB: leases so two agents never take the same "
            "work, and vector recall so nobody repeats a dead end."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="machine-readable output where applicable"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser(
        "init-schema", help="create tables and vector indexes", parents=[json_parent]
    )
    p_init.add_argument("--schema", help="path to 001_init.sql (default: auto-detect)")
    p_init.add_argument(
        "--repair-vector-indexes",
        action="store_true",
        help=(
            "rebuild a vector index whose op class does not match what recall() "
            "queries with (drops and recreates the index)"
        ),
    )

    sub.add_parser("status", help="counters for the configured swarm", parents=[json_parent])

    p_register = sub.add_parser(
        "register-agent",
        help="register a stable agent identity with framework and host",
        parents=[json_parent],
    )
    p_register.add_argument("--agent-id", required=True)
    p_register.add_argument("--framework", required=True)
    p_register.add_argument("--host", required=True)
    p_register.add_argument(
        "--capabilities-json",
        default="{}",
        help="JSON object describing capabilities (default: {})",
    )

    p_claim = sub.add_parser(
        "claim", help="take an exclusive lease on a resource", parents=[json_parent]
    )
    p_claim.add_argument("resource")
    p_claim.add_argument("--agent-id", required=True)
    p_claim.add_argument("--intent", required=True)
    p_claim.add_argument("--ttl", type=int, default=None, help="lease seconds")

    # Reachable since 2026-07-27. `Memory.heartbeat` has existed since the initial
    # release and README prescribes "claim/heartbeat/release", but no agent could
    # follow that: the verb was exposed on neither the CLI nor MCP. In the field run
    # `starmap-2026-07-27` every lease therefore ran on its TTL alone, and three
    # lapsed mid-task and were re-granted.
    p_heartbeat = sub.add_parser(
        "heartbeat", help="keep a lease alive while still working", parents=[json_parent]
    )
    p_heartbeat.add_argument("claim_id")

    p_release = sub.add_parser(
        "release", help="hand a lease back", parents=[json_parent]
    )
    p_release.add_argument("claim_id")

    p_who = sub.add_parser(
        "who-has", help="show the current holder of a resource", parents=[json_parent]
    )
    p_who.add_argument("resource")

    p_remember = sub.add_parser(
        "remember", help="record an attempt and its outcome", parents=[json_parent]
    )
    p_remember.add_argument("topic")
    p_remember.add_argument("--approach", required=True)
    p_remember.add_argument(
        "--outcome",
        required=True,
        choices=["success", "failure", "abandoned", "inconclusive"],
    )
    p_remember.add_argument("--evidence", required=True)
    p_remember.add_argument("--agent-id", default=None)

    p_recall = sub.add_parser(
        "recall", help="find prior attempts close to a query", parents=[json_parent]
    )
    p_recall.add_argument("query")
    p_recall.add_argument("--limit", type=int, default=5)
    p_recall.add_argument(
        "--outcome",
        action="append",
        dest="outcomes",
        choices=["success", "failure", "abandoned", "inconclusive"],
        help="restrict to these outcomes (repeatable)",
    )

    p_decide = sub.add_parser(
        "decide", help="record a decision with provenance", parents=[json_parent]
    )
    p_decide.add_argument("question")
    p_decide.add_argument("--choice", required=True)
    p_decide.add_argument("--rationale", required=True)
    p_decide.add_argument("--confidence", required=True, choices=["high", "medium", "low"])
    p_decide.add_argument(
        "--provenance",
        required=True,
        choices=["agent-inferred", "human-confirmed", "human-corrected"],
    )
    p_decide.add_argument("--agent-id", default=None)
    return parser


def _emit(payload: dict, as_json: bool, human: str) -> None:
    print(json.dumps(payload, default=str) if as_json else human)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        cfg = load_config()

        if args.command == "init-schema":
            results = apply_schema(
                cfg, args.schema, repair_vector_indexes=args.repair_vector_indexes
            )
            for status, statement in results:
                print(f"{status:<12} {statement}")
            print(f"{len(results)} statement(s) applied")
            return 0

        with Roshambo(cfg) as roshambo:
            if args.command == "status":
                st = roshambo.status()
                _emit(
                    st.__dict__,
                    args.json,
                    f"swarm={cfg.swarm_id} agents={st.agents} "
                    f"active_claims={st.active_claims} trails={st.trails} "
                    f"failures={st.failures} facts={st.facts}",
                )
                return 0

            if args.command == "register-agent":
                capabilities = json.loads(args.capabilities_json)
                if not isinstance(capabilities, dict):
                    raise ValueError("--capabilities-json must contain a JSON object")
                agent_id = roshambo.register_agent(
                    agent_id=args.agent_id,
                    framework=args.framework,
                    host=args.host,
                    capabilities=capabilities,
                )
                _emit(
                    {
                        "registered": True,
                        "agent_id": agent_id,
                        "framework": args.framework,
                        "host": args.host,
                    },
                    args.json,
                    f"registered {agent_id} ({args.framework} on {args.host})",
                )
                return 0

            if args.command == "claim":
                result = roshambo.claim(args.resource, args.agent_id, args.intent, args.ttl)
                if isinstance(result, Claim):
                    _emit(
                        {"granted": True, **result.__dict__},
                        args.json,
                        f"granted {result.claim_id} until {result.expires_at.isoformat()}",
                    )
                    return 0
                _emit(
                    {"granted": False, **result.__dict__},
                    args.json,
                    f"denied: held by {result.held_by} until "
                    f"{result.expires_at.isoformat()} — {result.intent}",
                )
                return 3

            if args.command == "heartbeat":
                alive = roshambo.heartbeat(args.claim_id)
                _emit(
                    {"alive": alive},
                    args.json,
                    "lease extended"
                    if alive
                    else "lease has lapsed and is not renewable — it may be somebody else's now",
                )
                return 0 if alive else 3

            if args.command == "release":
                ok = roshambo.release(args.claim_id)
                _emit(
                    {"released": ok},
                    args.json,
                    "released" if ok else "no such claim (already released or taken over)",
                )
                return 0 if ok else 3

            if args.command == "who-has":
                held = roshambo.who_has(args.resource)
                if held is None:
                    _emit({"held": False}, args.json, "free")
                    return 0
                _emit(
                    {"held": True, **held.__dict__},
                    args.json,
                    f"{held.agent_id} until {held.expires_at.isoformat()} — {held.intent}",
                )
                return 0

            if args.command == "remember":
                trail = roshambo.remember(
                    topic=args.topic,
                    approach=args.approach,
                    outcome=args.outcome,
                    evidence=args.evidence,
                    agent_id=args.agent_id,
                )
                _emit({**trail.__dict__}, args.json, f"remembered {trail.trail_id}")
                return 0

            if args.command == "recall":
                hits = roshambo.recall(args.query, args.limit, args.outcomes)
                if args.json:
                    print(
                        json.dumps(
                            [
                                {
                                    "distance": h.distance,
                                    "strength": h.strength,
                                    **h.trail.__dict__,
                                }
                                for h in hits
                            ],
                            default=str,
                        )
                    )
                    return 0
                if not hits:
                    print("nothing remembered that resembles this")
                    return 0
                for rank, hit in enumerate(hits, start=1):
                    print(
                        f"{rank}. [{hit.trail.outcome}] {hit.trail.topic} "
                        f"(distance {hit.distance:.4f}, strength {hit.strength:.1f})"
                    )
                    print(f"   approach: {hit.trail.approach}")
                    print(f"   evidence: {hit.trail.evidence}")
                return 0

            if args.command == "decide":
                decision = roshambo.decide(
                    question=args.question,
                    choice=args.choice,
                    rationale=args.rationale,
                    confidence=args.confidence,
                    provenance=args.provenance,
                    agent_id=args.agent_id,
                )
                _emit(
                    {**decision.__dict__},
                    args.json,
                    f"decided {decision.decision_id}: {decision.choice}",
                )
                return 0

        return 2

    except RoshamboError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Most commonly a missing AWS credential on the embedding path. A stack trace
        # from deep inside a cloud SDK is a poor answer to `roshambo recall "..."`.
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        if "credential" in str(exc).lower():
            print(
                "hint: set ROSHAMBO_EMBEDDING_PROVIDER=placeholder to run offline "
                "(lexical matching only, not a semantic model)",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
