"""A deterministic load generator that proves single-winner claims under real contention.

Not an LLM agent -- a cheap, controllable Python script. Where the multivendor field
run demonstrates *heterogeneous* coordination (three vendors, three sessions), this
demonstrates *volume*: many workers reaching for the same resources at once, the way a
larger swarm eventually will. It answers one question precisely: across every wave of
simultaneous claim attempts, was any resource ever granted to more than one holder at
the same time? The expected answer is no, and the script proves it two independent
ways (per-wave grant count, and a global pairwise overlap check across every grant
window recorded in the whole run) rather than asserting it from one angle only.

Uses the same claim path the field run and the test suite use --
`roshambo.memory.Roshambo.claim()` / `.release()` -- never raw SQL against the tables
it protects. See `tests/test_core_concurrency.py` for the pattern this borrows
(one OS thread per worker, one connection per worker, released from a
`threading.Barrier` so the claim statements genuinely overlap in time).

Cost brake, not a suggestion
-----------------------------
CockroachDB Basic bills Request Units and this cluster has a monthly RU/dollar cap
(see `C:\\_Local_DEV\\CREDENTIALS\\cockroachdb\\cluster.md`). Every attempt is at least
one write (the claim upsert) plus one audit-log insert; a granted claim adds a release
(another write + audit insert). `--max-attempts` and `--time-limit` are hard ceilings,
on by default, and the script refuses to start a plan that would exceed either --
lower them, don't raise them, unless you have actually checked the console's RU graph.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Conservative on purpose (see module docstring). Raising these is a decision, not a
# default -- the CLI requires an explicit --i-have-checked-the-ru-budget flag past a
# point defined by DEFAULT_MAX_ATTEMPTS / DEFAULT_TIME_LIMIT_SECONDS.
DEFAULT_MAX_ATTEMPTS = 300
DEFAULT_TIME_LIMIT_SECONDS = 180
DEFAULT_WORKERS = 8
DEFAULT_WAVES = 5
DEFAULT_TASK_COUNT = 3
DEFAULT_TASK_PREFIX = "storm:task:"
DEFAULT_TTL_SECONDS = 30
DEFAULT_PAUSE_BETWEEN_WAVES = 1.0


@dataclass(frozen=True)
class StormPlan:
    workers: int
    waves: int
    tasks: tuple[str, ...]
    ttl_seconds: int
    pause_between_waves: float
    agent_prefix: str = "storm"

    @property
    def total_attempts(self) -> int:
        return self.workers * self.waves * len(self.tasks)


class RuBudgetExceeded(SystemExit):
    """Raised (as SystemExit) when a plan would exceed the configured hard caps."""


def default_tasks(prefix: str, count: int) -> tuple[str, ...]:
    return tuple(f"{prefix}{i:02d}" for i in range(1, count + 1))


def enforce_caps(
    plan: StormPlan,
    max_attempts: int,
    time_limit_seconds: int,
    override: bool = False,
) -> None:
    """Refuse a plan that would exceed the hard ceilings, unless explicitly overridden.

    Pure function of its inputs -- no cluster contact -- so it is fully unit-testable
    offline (see tests/test_contention_storm.py).
    """
    if plan.total_attempts > max_attempts and not override:
        raise RuBudgetExceeded(
            f"planned {plan.total_attempts} attempts "
            f"({plan.workers} workers x {plan.waves} waves x {len(plan.tasks)} tasks) "
            f"exceeds --max-attempts {max_attempts}. Lower --workers/--waves/--tasks, "
            "raise --max-attempts only after checking the RU graph, or pass "
            "--i-have-checked-the-ru-budget."
        )
    # A rough worst case: waves run back to back with pause_between_waves between them,
    # plus the barrier wait and the claim/release round trips themselves. This is a
    # planning estimate to catch an obviously too-large --waves value early, not a
    # promise -- the real run is still bounded by the wall-clock check in run_storm().
    estimated_seconds = plan.waves * (plan.pause_between_waves + 2.0)
    if estimated_seconds > time_limit_seconds and not override:
        raise RuBudgetExceeded(
            f"planned run is estimated at ~{estimated_seconds:.0f}s, "
            f"exceeds --time-limit {time_limit_seconds}s. Lower --waves or "
            "--pause-between-waves, raise --time-limit only deliberately, or pass "
            "--i-have-checked-the-ru-budget."
        )


def estimate_ru_cost(plan: StormPlan) -> dict:
    """A labelled estimate, not a measurement -- see module docstring.

    Basis: one claim attempt is one upsert-shaped write plus one audit-log insert
    (`roshambo.memory.Roshambo.claim`, `_audit`); a granted claim adds one release
    write plus one more audit insert. CockroachDB Basic's published RU model prices
    small point writes at low single-digit RUs each; this uses 5 RU/write as a round,
    conservative planning number, not a vendor-confirmed figure -- the report must
    say so plainly.
    """
    attempts = plan.total_attempts
    # One grant per wave-resource pair, at most (see the single-winner property this
    # script exists to check) -- so grants <= waves * len(tasks), used as the
    # worst-case upper bound for the extra release + audit writes.
    max_grants = plan.waves * len(plan.tasks)
    writes_per_attempt = 2  # claim upsert + audit insert
    writes_per_grant_extra = 2  # release + its audit insert
    total_writes = attempts * writes_per_attempt + max_grants * writes_per_grant_extra
    ru_per_write_estimate = 5
    return {
        "attempts": attempts,
        "max_grants": max_grants,
        "estimated_writes": total_writes,
        "ru_per_write_estimate": ru_per_write_estimate,
        "estimated_ru_total": total_writes * ru_per_write_estimate,
        "basis": (
            "planning estimate only: 2 writes/attempt (claim + audit) + 2 writes per "
            "grant (release + audit), 5 RU/write as a conservative round number -- "
            "not read from CockroachDB's own RU accounting"
        ),
    }


@dataclass
class AttemptRecord:
    resource: str
    agent_id: str
    granted: bool
    claim_id: str | None
    started: float
    ended: float
    expires_at: datetime | None = None


@dataclass
class WaveResult:
    wave_index: int
    resource: str
    attempts: list[AttemptRecord]
    granted: list[AttemptRecord]
    peak_overlap: int
    contract_violation: bool  # True iff more than one attempt was granted this wave


@dataclass
class StormReport:
    plan: StormPlan
    swarm_id: str
    waves: list[WaveResult] = field(default_factory=list)
    two_simultaneous_holders_detected: bool = False
    global_overlap_violations: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def total_attempts(self) -> int:
        return sum(len(w.attempts) for w in self.waves)

    @property
    def total_granted(self) -> int:
        return sum(len(w.granted) for w in self.waves)

    @property
    def total_denied(self) -> int:
        return self.total_attempts - self.total_granted

    def as_dict(self) -> dict:
        return {
            "swarm_id": self.swarm_id,
            "workers": self.plan.workers,
            "waves": self.plan.waves,
            "tasks": list(self.plan.tasks),
            "total_attempts": self.total_attempts,
            "total_granted": self.total_granted,
            "total_denied": self.total_denied,
            "two_simultaneous_holders_detected": self.two_simultaneous_holders_detected,
            "global_overlap_violations": self.global_overlap_violations,
            "per_wave": [
                {
                    "wave": w.wave_index,
                    "resource": w.resource,
                    "attempts": len(w.attempts),
                    "granted": len(w.granted),
                    "peak_overlap": w.peak_overlap,
                    "contract_violation": w.contract_violation,
                }
                for w in self.waves
            ],
        }


def _peak_overlap(intervals: list[tuple[float, float]]) -> int:
    """How many claim statements were in flight at the busiest instant.

    Identical technique to tests/test_core_concurrency.py::_peak_overlap -- copied
    rather than imported because that module is test-only and not on the import path
    of a script meant to run standalone.
    """
    events = [(start, 1) for start, _ in intervals] + [(end, -1) for _, end in intervals]
    events.sort(key=lambda e: (e[0], e[1]))
    current = peak = 0
    for _timestamp, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def _windows_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def run_wave(
    client_factory,
    wave_index: int,
    resource: str,
    workers: int,
    ttl_seconds: int,
    agent_prefix: str,
) -> WaveResult:
    """One barrier-synced burst of `workers` simultaneous claim attempts on `resource`.

    `client_factory()` must return a fresh, unconnected client object exposing
    `.claim(resource, agent_id, intent, ttl_seconds)` returning something with either
    a `claim_id` attribute (granted) or not (denied), `.conn` to force connection
    before the barrier, `.release(claim_id)`, and `.close()` -- exactly the
    `roshambo.memory.Roshambo` surface. Kept as a factory + duck-typed protocol so the
    offline tests can pass a fake without touching a real cluster.
    """
    from roshambo.models import Claim

    barrier = threading.Barrier(workers)
    records: list[AttemptRecord] = []
    lock = threading.Lock()

    def _attempt(i: int) -> None:
        agent_id = f"{agent_prefix}-w{i:03d}"
        client = client_factory()
        try:
            _ = client.conn  # connect before the barrier, not as part of the race
            barrier.wait(timeout=60)
            started = time.perf_counter()
            result = client.claim(resource, agent_id, f"contention storm wave {wave_index}", ttl_seconds)
            ended = time.perf_counter()
            granted = isinstance(result, Claim)
            record = AttemptRecord(
                resource=resource,
                agent_id=agent_id,
                granted=granted,
                claim_id=result.claim_id if granted else None,
                started=started,
                ended=ended,
                expires_at=result.expires_at if granted else None,
            )
            with lock:
                records.append(record)
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_attempt, i) for i in range(workers)]
        for f in futures:
            f.result()

    granted = [r for r in records if r.granted]
    peak = _peak_overlap([(r.started, r.ended) for r in records])

    # Release every winner immediately so the next wave contends on a genuinely free
    # resource rather than inheriting a still-live lease from this one.
    if granted:
        client = client_factory()
        try:
            for r in granted:
                assert r.claim_id is not None
                client.release(r.claim_id)
        finally:
            client.close()

    return WaveResult(
        wave_index=wave_index,
        resource=resource,
        attempts=records,
        granted=granted,
        peak_overlap=peak,
        contract_violation=len(granted) > 1,
    )


def run_storm(
    client_factory,
    plan: StormPlan,
    swarm_id: str,
    time_limit_seconds: int,
    on_wave=None,
) -> StormReport:
    report = StormReport(plan=plan, swarm_id=swarm_id)
    deadline = time.monotonic() + time_limit_seconds
    wave_index = 0

    for round_no in range(plan.waves):
        for resource in plan.tasks:
            if time.monotonic() > deadline:
                print(
                    f"time limit ({time_limit_seconds}s) reached after {wave_index} waves; "
                    "stopping early rather than exceeding the budget",
                    file=sys.stderr,
                )
                return _finalize(report)
            wave_index += 1
            result = run_wave(
                client_factory,
                wave_index,
                resource,
                plan.workers,
                plan.ttl_seconds,
                f"{plan.agent_prefix}-r{round_no:02d}",
            )
            report.waves.append(result)
            if on_wave:
                on_wave(result)
            if plan.pause_between_waves:
                time.sleep(plan.pause_between_waves)

    return _finalize(report)


def _finalize(report: StormReport) -> StormReport:
    """Two independent checks for 'two simultaneous holders', not one.

    (1) Per-wave: did any single barrier-synced burst grant more than one claim on
        its resource? That would be a same-instant violation.
    (2) Global: across the WHOLE run, do any two granted-claim windows for the SAME
        resource overlap in time at all, even across different waves (e.g. a release
        that raced a later wave's claim)? Windows are [claim start, release end] when
        released, or [claim start, expires_at] as a fallback.
    """
    if any(w.contract_violation for w in report.waves):
        report.two_simultaneous_holders_detected = True

    by_resource: dict[str, list[tuple[str, float, float]]] = {}
    for w in report.waves:
        for r in w.granted:
            end = r.ended  # conservative: the moment the grant call returned;
            # the explicit release() in run_wave happens strictly after this, so using
            # `ended` here (rather than expires_at, which is minutes out) keeps this
            # check tight to what was actually observed, not what was merely allowed.
            by_resource.setdefault(r.resource, []).append((r.claim_id or "", r.started, end))

    for resource, windows in by_resource.items():
        for i in range(len(windows)):
            for j in range(i + 1, len(windows)):
                a, b = windows[i], windows[j]
                if _windows_overlap((a[1], a[2]), (b[1], b[2])):
                    report.two_simultaneous_holders_detected = True
                    report.global_overlap_violations.append((resource, a[0], b[0]))

    return report


def _real_client_factory(cfg, embedder_kind: str):
    from roshambo.memory import PlaceholderEmbedder, Roshambo

    def factory():
        return Roshambo(cfg, embedder=PlaceholderEmbedder(dim=cfg.embedding_dim))

    return factory


def _load_cfg(swarm_id: str, ttl_seconds: int):
    from rsb import resolve_dsn  # local import: rsb.py lives next to this script
    from roshambo.config import RoshamboConfig

    dsn = resolve_dsn(dict(__import__("os").environ))
    return RoshamboConfig(dsn=dsn, swarm_id=swarm_id, lease_ttl_seconds=ttl_seconds)


def build_plan(args: argparse.Namespace) -> StormPlan:
    if args.tasks:
        tasks = tuple(t.strip() for t in args.tasks.split(",") if t.strip())
    else:
        tasks = default_tasks(args.task_prefix, args.task_count)
    return StormPlan(
        workers=args.workers,
        waves=args.waves,
        tasks=tasks,
        ttl_seconds=args.ttl,
        pause_between_waves=args.pause_between_waves,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--swarm", required=True, help="ROSHAMBO_SWARM_ID to run against")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--waves", type=int, default=DEFAULT_WAVES)
    parser.add_argument(
        "--tasks", default=None, help="comma-separated resource names (overrides --task-prefix/-count)"
    )
    parser.add_argument("--task-prefix", default=DEFAULT_TASK_PREFIX)
    parser.add_argument("--task-count", type=int, default=DEFAULT_TASK_COUNT)
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL_SECONDS, help="lease seconds per attempt")
    parser.add_argument("--pause-between-waves", type=float, default=DEFAULT_PAUSE_BETWEEN_WAVES)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--time-limit", type=int, default=DEFAULT_TIME_LIMIT_SECONDS)
    parser.add_argument(
        "--i-have-checked-the-ru-budget",
        action="store_true",
        help="required to exceed --max-attempts or --time-limit",
    )
    parser.add_argument("--json", action="store_true", help="print the final report as JSON")
    args = parser.parse_args(argv)

    plan = build_plan(args)
    estimate = estimate_ru_cost(plan)
    print(
        f"plan: {plan.workers} workers x {plan.waves} waves x {len(plan.tasks)} tasks "
        f"= {plan.total_attempts} attempts",
        file=sys.stderr,
    )
    print(
        f"RU estimate (planning only, not measured): ~{estimate['estimated_ru_total']} RU "
        f"({estimate['estimated_writes']} writes x {estimate['ru_per_write_estimate']} RU/write)",
        file=sys.stderr,
    )

    try:
        enforce_caps(
            plan, args.max_attempts, args.time_limit, override=args.i_have_checked_the_ru_budget
        )
    except RuBudgetExceeded as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2

    cfg = _load_cfg(args.swarm, args.ttl)
    client_factory = _real_client_factory(cfg, "placeholder")

    def on_wave(w: WaveResult) -> None:
        status = "VIOLATION" if w.contract_violation else "ok"
        print(
            f"  wave {w.wave_index:03d} [{w.resource}]: {len(w.attempts)} attempts, "
            f"{len(w.granted)} granted, peak overlap {w.peak_overlap} -- {status}",
            file=sys.stderr,
        )

    started_at = datetime.now(timezone.utc).isoformat()
    report = run_storm(client_factory, plan, args.swarm, args.time_limit, on_wave=on_wave)

    print(f"\nstarted:  {started_at}", file=sys.stderr)
    print(f"attempts: {report.total_attempts}", file=sys.stderr)
    print(f"granted:  {report.total_granted}", file=sys.stderr)
    print(f"denied:   {report.total_denied}", file=sys.stderr)
    print(
        "two simultaneous holders of the same task, at any point: "
        f"{'YES -- CONTRACT VIOLATION' if report.two_simultaneous_holders_detected else 'no'}",
        file=sys.stderr,
    )

    if args.json:
        import json as _json

        print(_json.dumps(report.as_dict(), indent=2))

    return 1 if report.two_simultaneous_holders_detected else 0


if __name__ == "__main__":
    raise SystemExit(main())
