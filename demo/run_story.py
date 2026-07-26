"""The four beats of MANIFEST.md section 7, playable one at a time.

    python demo/run_story.py --beat 1     # collision: three agents, one winner
    python demo/run_story.py --beat 2     # the winner fails, and says so
    python demo/run_story.py --beat 3     # a new agent recalls that failure first
    python demo/run_story.py --beat 4     # a lease lapses and is taken over
    python demo/run_story.py --all        # all four, no state file touched
    python demo/run_story.py --measure --rounds 10   # the phase-4 acceptance number

Stepwise on purpose. The demo web app polls, so running one beat at a time is what
lets a person watch the UI change between beats -- for a screenshot, for a recording,
or just to believe it. ``--beat N`` therefore carries what the next beat needs (the
winner's identity, its lease, the failure it wrote) through a small state file;
``--all`` passes the same thing in memory and writes nothing.

**Three separate agents, three separate connections, one process.** The three racers
are threads that meet at a ``threading.Barrier`` and then each call ``claim()`` on
their own ``Roshambo`` instance -- the class is documented as not thread safe, so
sharing one would be measuring the wrong thing. Three OS processes would be a
stronger claim about *machines*; three concurrent transactions against one cluster is
what actually decides the winner, and that is what this measures. The 20-way
concurrency test in ``tests/test_core_concurrency.py`` covers the same property at
higher contention.

**Which embedder must be running.** Beat 3 is a retrieval beat, so it needs the one
offline embedder that has retrieval signal: ``ROSHAMBO_EMBEDDING_PROVIDER=placeholder``
(``roshambo.memory.PlaceholderEmbedder``, hashes word tokens and character trigrams --
the same one ``tests/conftest.py`` uses). The other offline embedder, ``local``
(``roshambo.embeddings.DeterministicEmbedder``), hashes the whole text into
uncorrelated vectors and makes ``recall()`` rank arbitrarily. What it finds is
**lexical overlap, not semantic similarity** -- Bedrock's Titan embeddings are the
semantic path and have not been exercised yet (docs/EVIDENCE-cloud.md).

**Why this does not reuse the existing workers.** ``demo/run_collision_demo.py``
races the AWS Lambda handler against ``demo/local_agent_worker.py`` and is the better
demo of *two vendors*; it stays as it is. It cannot serve this script, because
``roshambo.aws.worker`` resolves its embedder through ``roshambo.embeddings.
get_embedder()``, which accepts only ``bedrock``/``local`` (CONTRACT.md) and raises on
``placeholder``. ``local_agent_worker.run_local_worker`` also releases its lease
immediately and always reports success on the winning path, while this story needs the
winner to *hold* its lease into beat 2 and then fail.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

DEFAULT_STATE_PATH = Path(__file__).resolve().parent / ".story-state.json"

#: What the whole story is about. One topic across all four beats, because beat 3 is
#: only meaningful if the new agent's task really is the same job in different words.
TOPIC = "migrate the billing schema"

#: What every racer says it is about to do. Same job, one label per runtime -- that is
#: why they collide, and the label is what the losers get told.
def _intent(runtime: Runtime) -> str:
    return f"apply the pending billing schema migration ({runtime.framework} run)"

#: Beat 3's query. Deliberately *not* the sentence above: same job, different wording,
#: which is the point of the beat. Quoted verbatim in docs/EVIDENCE-demo.md so a reader
#: can judge the rewording instead of taking anyone's word for it.
REWORDED_QUERY = "roll out the pending schema change for billing on the live database"

PLACEHOLDER_PROVIDER = "placeholder"


@dataclass(frozen=True)
class Runtime:
    """One agent runtime as it appears in the ``agents`` table.

    Three different ``framework`` values, not three copies of one: Roshambo's claim is
    coordination between agents that do not know each other. The labels are generic on
    purpose -- this repository does not integrate with any specific vendor product, and
    ``demo/local_agent_worker.py`` already established that convention. Hosts are
    synthetic; a real machine name does not belong in a public repo or a pitch video.
    """

    framework: str
    host: str


RACERS: tuple[Runtime, ...] = (
    Runtime("local-cli-agent", "on-prem-batch-node-3"),
    Runtime("mcp-agent", "mcp-gateway-eu-central-1"),
    Runtime("notebook-agent", "analytics-notebook-07"),
)

#: Beat 3's agent: same kind of runtime as one of the racers, different session. The
#: script says "ein *neuer* Agent (frische Session, kein Kontext)" -- not a new vendor.
SUCCESSOR = Runtime("local-cli-agent", "on-prem-batch-node-7")


# ------------------------------------------------------------------------- helpers


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resource_for(cfg: Any, suffix: str) -> str:
    """An S3-prefix-shaped coordination key, unique per run.

    A prefix rather than a filename, following ``demo/run_collision_demo.py``: what is
    being coordinated is an arbitrary named resource, and "the place several agents
    would write into" is a concrete example of one that is not a file. Nothing is
    written to S3 here.
    """
    bucket = getattr(cfg, "s3_bucket", None) or "roshambo-demo-bucket"
    return f"s3-prefix:{bucket}/agent-runs/{suffix}/"


def _client(cfg: Any) -> Any:
    from roshambo.memory import Roshambo

    return Roshambo(cfg)


def _embedder_note(cfg: Any) -> str:
    if cfg.embedding_provider == PLACEHOLDER_PROVIDER:
        return (
            "PlaceholderEmbedder (lexical: word tokens + character trigrams). "
            "Ranking reflects vocabulary overlap, NOT semantic similarity."
        )
    if cfg.embedding_provider == "local":
        return (
            "DeterministicEmbedder -- hashes the whole text into UNCORRELATED vectors. "
            "recall() ranking is meaningless with this provider."
        )
    return f"{cfg.embedding_provider} (see roshambo.embeddings.get_embedder)"


def _warn_about_embedder(cfg: Any) -> None:
    if cfg.embedding_provider != PLACEHOLDER_PROVIDER:
        print(
            f"WARNING: ROSHAMBO_EMBEDDING_PROVIDER={cfg.embedding_provider!r}. "
            f"Beat 3 needs {PLACEHOLDER_PROVIDER!r} to rank at all -- {_embedder_note(cfg)}",
            file=sys.stderr,
        )


# --------------------------------------------------------------------------- race


def _race(
    cfg: Any,
    runtimes: tuple[Runtime, ...],
    *,
    resource: str,
    topic: str,
    intent_for: Callable[[int, Runtime], str],
    ttl_seconds: int | None = None,
    write_trails: bool = True,
    winner_releases: bool = False,
) -> list[dict]:
    """Have every runtime in `runtimes` go for `resource` at the same moment.

    Each thread builds its own client and registers its own agent *before* the barrier,
    so the contended step is `claim()` alone and not "whoever finished setting up
    first". Returns one result dict per runtime, in the order given.
    """
    count = len(runtimes)
    barrier = threading.Barrier(count)
    results: list[dict | None] = [None] * count

    def one(index: int, runtime: Runtime) -> None:
        result: dict[str, Any] = {"framework": runtime.framework, "host": runtime.host}
        client = None
        try:
            client = _client(cfg)
            agent_id = client.register_agent(
                framework=runtime.framework,
                host=runtime.host,
                capabilities={"runtime": runtime.framework, "demo": "run_story"},
            )
            intent = intent_for(index, runtime)
            result.update(agent_id=agent_id, intent=intent)

            # Wait for the others to finish registering. A timeout rather than a bare
            # wait(): if one thread dies during setup the rest must fail loudly instead
            # of hanging the run forever.
            barrier.wait(timeout=60)

            started = time.perf_counter()
            kwargs: dict[str, Any] = {
                "resource": resource,
                "agent_id": agent_id,
                "intent": intent,
            }
            if ttl_seconds is not None:
                kwargs["ttl_seconds"] = ttl_seconds
            outcome = client.claim(**kwargs)
            result["claim_ms"] = round((time.perf_counter() - started) * 1000, 1)

            if hasattr(outcome, "held_by"):  # ClaimDenied -- duck-typed, as elsewhere
                result.update(
                    status="denied",
                    held_by=outcome.held_by,
                    holder_intent=outcome.intent,
                    holder_expires_at=outcome.expires_at.isoformat(),
                )
                if write_trails:
                    # The denial becomes a piece of memory, not just a console line:
                    # a later recall() can surface "somebody already tried to take this".
                    trail = client.remember(
                        topic=topic,
                        approach=intent,
                        outcome="abandoned",
                        evidence=(
                            f"asked for {resource} and was turned away: held by "
                            f"{outcome.held_by} ({outcome.intent}), lease runs to "
                            f"{outcome.expires_at.isoformat()}. Did not duplicate the work."
                        ),
                        agent_id=agent_id,
                        # Structured, so demo/queries.py:recent_denials can render the
                        # denial without parsing prose. The holder's *framework* is not
                        # in here on purpose -- a denied worker never learns it; the UI
                        # resolves it by joining `agents`.
                        detail={
                            "kind": "claim-denied",
                            "resource": resource,
                            "held_by": outcome.held_by,
                            "holder_intent": outcome.intent,
                            "holder_expires_at": outcome.expires_at.isoformat(),
                        },
                    )
                    result["trail_id"] = trail.trail_id
            else:
                result.update(
                    status="granted",
                    claim_id=outcome.claim_id,
                    expires_at=outcome.expires_at.isoformat(),
                )
                if winner_releases:
                    result["released"] = client.release(outcome.claim_id)
        except Exception as exc:  # noqa: BLE001 -- one thread's failure is a result, not a crash
            result.update(status="error", error=f"{type(exc).__name__}: {exc}")
            barrier.abort()
        finally:
            if client is not None:
                client.close()
            results[index] = result

    threads = [
        threading.Thread(target=one, args=(i, rt), name=f"racer-{rt.framework}")
        for i, rt in enumerate(runtimes)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return [r for r in results if r is not None]


def _verdict(results: list[dict], *, expected: int) -> dict:
    """Judge one race against the phase-4 acceptance criterion.

    "Exactly one winner" is only half of it. MANIFEST.md section 7 also asks that the
    others be turned away *naming who is working*, so each denial has to point at the
    actual winner -- a denial naming somebody else would mean two leases existed.
    """
    granted = [r for r in results if r.get("status") == "granted"]
    denied = [r for r in results if r.get("status") == "denied"]
    errors = [r for r in results if r.get("status") == "error"]

    problems: list[str] = []
    if len(granted) != 1:
        problems.append(f"expected exactly 1 winner, got {len(granted)}")
    if len(denied) != expected - 1:
        problems.append(f"expected {expected - 1} denials, got {len(denied)}")
    if errors:
        problems.append(f"{len(errors)} worker(s) errored: {[e.get('error') for e in errors]}")
    if len(granted) == 1:
        winner_id = granted[0]["agent_id"]
        wrong = [d for d in denied if d.get("held_by") != winner_id]
        if wrong:
            problems.append(f"{len(wrong)} denial(s) named someone other than the winner")
        mismatched = [d for d in denied if d.get("holder_intent") != granted[0].get("intent")]
        if mismatched:
            problems.append(f"{len(mismatched)} denial(s) reported the wrong holder intent")

    return {
        "winners": len(granted),
        "denials": len(denied),
        "errors": len(errors),
        "ok": not problems,
        "problems": problems,
        "winner": granted[0] if len(granted) == 1 else None,
    }


# -------------------------------------------------------------------------- beats


def beat_1(cfg: Any, state: dict) -> tuple[dict, dict]:
    """Collision. Three agents, one resource, one winner -- and two informed refusals.

    The winner keeps its lease: beat 2 is that same agent running into a wall while
    still holding it, and a screenshot taken between the two beats should show the
    claim and the two denials side by side.
    """
    resource = _resource_for(cfg, f"story-{uuid.uuid4().hex[:8]}")
    results = _race(
        cfg,
        RACERS,
        resource=resource,
        topic=TOPIC,
        intent_for=lambda i, rt: _intent(rt),
        write_trails=True,
    )
    verdict = _verdict(results, expected=len(RACERS))

    if verdict["winner"] is not None:
        winner = verdict["winner"]
        state = {
            **state,
            "swarm_id": cfg.swarm_id,
            "topic": TOPIC,
            "resource": resource,
            "winner": winner,
            "beat_1_at": _now(),
        }
    report = {
        "beat": 1,
        "name": "collision",
        "resource": resource,
        "results": results,
        **verdict,
    }
    return report, state


def beat_2(cfg: Any, state: dict) -> tuple[dict, dict]:
    """The winner hits a dead end and leaves a usable record of it.

    ``outcome="failure"`` with the actual error in ``evidence``: that text is what beat
    3 has to be able to find. The lease is released here -- the agent is done, it just
    did not succeed, and holding a lease it is not working under would be a lie to
    everyone else in the swarm.
    """
    winner = state.get("winner")
    if not winner:
        raise SystemExit("beat 2 needs beat 1's winner -- run --beat 1 first (or use --all)")

    approach = f"{winner['intent']}, run straight against the primary with no lock timeout"
    evidence = (
        "blocked behind a long-running analytics report holding a lock on "
        "billing_invoices; aborted after 30s with SQLSTATE 55P03 (lock_not_available). "
        "The migration never applied and the table was left untouched."
    )

    client = _client(cfg)
    try:
        trail = client.remember(
            topic=state.get("topic", TOPIC),
            approach=approach,
            outcome="failure",
            evidence=evidence,
            agent_id=winner["agent_id"],
            detail={"kind": "dead-end", "resource": state.get("resource"), "sqlstate": "55P03"},
        )
        released = client.release(winner["claim_id"])
    finally:
        client.close()

    result = {
        "beat": 2,
        "name": "failure with consequence",
        "agent_id": winner["agent_id"],
        "framework": winner["framework"],
        "approach": approach,
        "evidence": evidence,
        "trail_id": trail.trail_id,
        "lease_released": released,
        "ok": bool(released),
        "problems": [] if released else ["release() returned False -- the lease was not ours"],
    }
    next_state = {
        **state,
        "failure_trail_id": trail.trail_id,
        "failure_approach": approach,
        "beat_2_at": _now(),
    }
    return result, next_state


def beat_3(cfg: Any, state: dict) -> tuple[dict, dict]:
    """A new agent asks the same question in different words -- and changes its plan.

    The beat the submission is actually about: memory that alters an action, not memory
    that gets displayed. Reports the full ranked list both unfiltered and filtered to
    failures, so a reader can see where the failure landed rather than being told.

    The ranking is **lexical**: see this module's docstring. The query is never tuned
    to make the beat succeed -- if the failure does not come back first, that is
    reported as a gap.
    """
    failure_trail_id = state.get("failure_trail_id")
    if not failure_trail_id:
        raise SystemExit("beat 3 needs beat 2's failure trail -- run --beat 2 first (or use --all)")

    client = _client(cfg)
    try:
        agent_id = client.register_agent(
            framework=SUCCESSOR.framework,
            host=SUCCESSOR.host,
            capabilities={"runtime": SUCCESSOR.framework, "demo": "run_story", "session": "new"},
        )

        def ranked(outcomes: list[str] | None) -> list[dict]:
            hits = client.recall(query=REWORDED_QUERY, limit=5, outcomes=outcomes)
            return [
                {
                    "rank": i + 1,
                    "trail_id": hit.trail.trail_id,
                    "outcome": hit.trail.outcome,
                    "distance": round(hit.distance, 4),
                    "approach": hit.trail.approach,
                    "is_the_failure": hit.trail.trail_id == failure_trail_id,
                }
                for i, hit in enumerate(hits)
            ]

        unfiltered = ranked(None)
        failures_only = ranked(["failure"])

        def rank_of(rows: list[dict]) -> int | None:
            return next((row["rank"] for row in rows if row["is_the_failure"]), None)

        rank_unfiltered = rank_of(unfiltered)
        rank_filtered = rank_of(failures_only)
        found = rank_filtered == 1

        decision = None
        reinforced = None
        if found:
            hit = next(row for row in failures_only if row["is_the_failure"])
            decision_obj = client.decide(
                question="How should the pending billing schema change be rolled out?",
                choice=(
                    "Set a short lock_timeout and retry on 55P03 instead of migrating "
                    "straight against the primary"
                ),
                rationale=(
                    f"recall({REWORDED_QUERY!r}) returned trail {failure_trail_id} at "
                    f"distance {hit['distance']} with outcome=failure: the direct route "
                    "already blocked behind a long-running report and aborted with 55P03. "
                    "Repeating it would fail the same way, so this run takes the other route."
                ),
                confidence="medium",
                provenance="agent-inferred",
                agent_id=agent_id,
            )
            decision = {
                "decision_id": decision_obj.decision_id,
                "question": decision_obj.question,
                "choice": decision_obj.choice,
                "rationale": decision_obj.rationale,
            }
            # Stigmergy: the trail earned its keep, so it gets heavier for the next agent.
            reinforced = client.reinforce(failure_trail_id)
    finally:
        client.close()

    problems = []
    if not found:
        problems.append(
            "the failure trail did not come back at rank 1 of the failure-filtered "
            "recall -- reported as measured, the query was not tuned to fix it"
        )

    result = {
        "beat": 3,
        "name": "memory that changes an action",
        "agent": {"agent_id": agent_id, **asdict(SUCCESSOR)},
        "embedder": _embedder_note(cfg),
        "original_approach": state.get("failure_approach"),
        "reworded_query": REWORDED_QUERY,
        "rank_unfiltered": rank_unfiltered,
        "rank_failures_only": rank_filtered,
        "ranking_unfiltered": unfiltered,
        "ranking_failures_only": failures_only,
        "decision": decision,
        "trail_strength_after_reinforce": reinforced,
        "ok": found,
        "problems": problems,
    }
    return result, {**state, "beat_3_at": _now()}


def beat_4(
    cfg: Any,
    state: dict,
    *,
    ttl_seconds: int = 6,
    takeover_ttl: int = 300,
    poll_seconds: float = 1.0,
) -> tuple[dict, dict]:
    """A holder goes silent. The lease lapses on its own and somebody else picks it up.

    The disturbance is modelled as the honest worst case for a coordinator: the holder
    neither releases nor heartbeats -- it is simply gone. Nothing cleans up after it;
    the lease expires because it was written with an expiry, and the next ``claim()``
    that arrives afterwards succeeds.

    All timing is read off the *server's* clock (a claim's ``expires_at`` minus the TTL
    it was granted with), so nothing here depends on this machine's clock agreeing with
    the cluster's. The taking-over agent keeps its lease deliberately: it leaves the
    demo UI with something live to show.
    """
    resource = _resource_for(cfg, f"failover-{uuid.uuid4().hex[:8]}")
    holder, successor = RACERS[1], RACERS[2]

    gone = _client(cfg)
    taker = _client(cfg)
    try:
        holder_id = gone.register_agent(
            framework=holder.framework, host=holder.host, capabilities={"demo": "run_story"}
        )
        taker_id = taker.register_agent(
            framework=successor.framework, host=successor.host, capabilities={"demo": "run_story"}
        )

        lease = gone.claim(
            resource=resource,
            agent_id=holder_id,
            intent="rebuild the billing rollup for the current period",
            ttl_seconds=ttl_seconds,
        )
        if hasattr(lease, "held_by"):
            raise SystemExit(f"beat 4 could not take the first lease on {resource}")
        # From here on `gone` does nothing at all: no heartbeat, no release. That is the
        # outage.
        expires_at = lease.expires_at

        first = taker.claim(
            resource=resource,
            agent_id=taker_id,
            intent="rebuild the billing rollup for the current period (takeover)",
            ttl_seconds=takeover_ttl,
        )
        blocked_while_valid = hasattr(first, "held_by")
        attempts = 1
        takeover = None if blocked_while_valid else first

        deadline = time.monotonic() + ttl_seconds + 30
        while takeover is None and time.monotonic() < deadline:
            time.sleep(poll_seconds)
            attempts += 1
            candidate = taker.claim(
                resource=resource,
                agent_id=taker_id,
                intent="rebuild the billing rollup for the current period (takeover)",
                ttl_seconds=takeover_ttl,
            )
            if not hasattr(candidate, "held_by"):
                takeover = candidate

        if takeover is None:
            problems = [f"no takeover within {ttl_seconds + 30}s after the lease expired"]
            taken_at = None
            delay = None
        else:
            problems = []
            # Server-clock acquisition time of the takeover, derived from the TTL it was
            # granted with -- Claim carries no acquired_at.
            taken_at = takeover.expires_at - timedelta(seconds=takeover_ttl)
            delay = round((taken_at - expires_at).total_seconds(), 3)
            if delay < 0:
                problems.append(
                    f"takeover was granted {abs(delay)}s BEFORE the old lease expired -- "
                    "two agents would have held the same resource"
                )
        if not blocked_while_valid:
            problems.append(
                "the first takeover attempt was granted while the lease was still valid"
            )
    finally:
        gone.close()
        taker.close()

    result = {
        "beat": 4,
        "name": "lease lapses, work is taken over",
        "resource": resource,
        "abandoned_by": {"agent_id": holder_id, **asdict(holder)},
        "taken_over_by": {"agent_id": taker_id, **asdict(successor)},
        "lease_ttl_seconds": ttl_seconds,
        "blocked_while_lease_was_valid": blocked_while_valid,
        "expired_at": expires_at.isoformat(),
        "taken_over_at": None if taken_at is None else taken_at.isoformat(),
        "takeover_delay_seconds": delay,
        "claim_attempts": attempts,
        "note": "the taking-over agent keeps its lease, so the UI has a live claim to show",
        "ok": not problems,
        "problems": problems,
    }
    return result, {**state, "beat_4_at": _now()}


BEATS: dict[int, Callable[[Any, dict], tuple[dict, dict]]] = {
    1: beat_1,
    2: beat_2,
    3: beat_3,
    4: beat_4,
}


# ---------------------------------------------------------------------- measuring


def measure(cfg: Any, rounds: int) -> dict:
    """The phase-4 acceptance number: N races, each judged on its own.

    Runs in its own swarm (``<swarm_id>-measure``) so the story's swarm keeps the
    counters and trails a screenshot is supposed to show, and a fresh resource per
    round so round 2 does not simply find round 1's lease still held -- that would
    report zero winners and mean nothing.
    """
    measure_cfg = replace(cfg, swarm_id=f"{cfg.swarm_id}-measure")
    rows = []
    for index in range(1, rounds + 1):
        resource = _resource_for(measure_cfg, f"measure-{uuid.uuid4().hex[:8]}")
        started = time.perf_counter()
        results = _race(
            measure_cfg,
            RACERS,
            resource=resource,
            topic=TOPIC,
            intent_for=lambda i, rt, round_no=index: (
                f"round {round_no}: {rt.framework} handling this resource"
            ),
            write_trails=True,
        )
        verdict = _verdict(results, expected=len(RACERS))
        rows.append(
            {
                "round": index,
                "resource": resource,
                "winner_framework": (verdict["winner"] or {}).get("framework"),
                "elapsed_s": round(time.perf_counter() - started, 2),
                **{k: verdict[k] for k in ("winners", "denials", "errors", "ok", "problems")},
            }
        )
        print(
            f"round {index}/{rounds}: {rows[-1]['winners']} winner, "
            f"{rows[-1]['denials']} denials, ok={rows[-1]['ok']} "
            f"({rows[-1]['winner_framework']}, {rows[-1]['elapsed_s']}s)",
            file=sys.stderr,
        )

    passed = sum(1 for row in rows if row["ok"])
    wins_by: dict[str, int] = {}
    for row in rows:
        if row["winner_framework"]:
            wins_by[row["winner_framework"]] = wins_by.get(row["winner_framework"], 0) + 1
    return {
        "measurement": "three concurrent workers, one contested resource",
        "swarm_id": measure_cfg.swarm_id,
        "workers_per_round": len(RACERS),
        "concurrency": "3 threads, 3 connections, 1 process; barrier-synchronised claim()",
        "rounds": rounds,
        "rounds_passed": passed,
        "rounds_failed": rounds - passed,
        "wins_by_framework": wins_by,
        "ok": passed == rounds,
        "detail": rows,
    }


# ------------------------------------------------------------------------ plumbing


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"state file {path} is not readable JSON: {exc}") from exc


def _save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--beat", type=int, choices=sorted(BEATS), help="play one beat")
    group.add_argument("--all", action="store_true", help="play all four, touching no state file")
    group.add_argument("--measure", action="store_true", help="repeat the collision and judge it")
    parser.add_argument("--rounds", type=int, default=10, help="rounds for --measure (default 10)")
    parser.add_argument(
        "--state",
        type=Path,
        default=Path(os.environ.get("ROSHAMBO_STORY_STATE") or DEFAULT_STATE_PATH),
        help="where --beat N carries context between runs",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from roshambo.config import load_config

    args = _build_parser().parse_args(argv)
    try:
        cfg = load_config()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _warn_about_embedder(cfg)
    print(f"swarm_id={cfg.swarm_id}  embedder={_embedder_note(cfg)}", file=sys.stderr)

    if args.measure:
        report = measure(cfg, args.rounds)
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["ok"] else 2

    if args.all:
        state: dict = {}
        reports = []
        for number in sorted(BEATS):
            report, state = BEATS[number](cfg, state)
            reports.append(report)
            print(f"beat {number} ({report['name']}): ok={report['ok']}", file=sys.stderr)
        print(json.dumps({"swarm_id": cfg.swarm_id, "beats": reports}, indent=2, default=str))
        return 0 if all(r["ok"] for r in reports) else 2

    state = _load_state(args.state) if args.beat > 1 else {}
    report, state = BEATS[args.beat](cfg, state)
    _save_state(args.state, state)
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
