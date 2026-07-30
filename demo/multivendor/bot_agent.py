"""BotAgent -- a protocol-conformant, non-LLM participant, and Roshambo's own stress test.

What this is, and why it exists
--------------------------------
Every other participant in `demo/multivendor/` is a real vendor CLI (Claude Code,
Codex, Antigravity) driven by a real language model. BotAgent is neither: it is a
small, deterministic Python loop that speaks exactly the same protocol -- it
registers, claims, holds briefly, releases -- through the same client class the
field run and the test suite use (`roshambo.memory.Roshambo`), never a shortcut
around it. It costs no tokens, runs in seconds, and scales to as many instances as
you like.

That is the point being demonstrated, not a limitation of the demo: Roshambo
coordinates *whoever speaks the protocol*, not a fixed set of vendors. A 200-line
script sits at the same table as three different AI companies' agents and is subject
to the same serializable leases. This widens the claim from "coordinates AI agents"
to "coordinates any concurrent process" -- and it means anyone can verify the core
guarantee (exactly one winner, ever, no matter how many simultaneous claimants)
without an LLM key, a field run, or a wait.

Two modes, one core
--------------------
Both modes run the identical register -> claim -> hold -> release loop per bot,
with the same adaptive rate control. They differ only in who else is on the swarm
and what the summary is for.

  --dry-run   A self-contained tournament: BotAgent A/B/C/... claim against *each
              other only*, on their own fresh swarm by default. No LLM agents, no
              files, no project -- pure coordination proof. This is the one anyone
              evaluating the submission can run themselves in well under a minute:

                  python bot_agent.py --dry-run --bots 3

  (default)   Storm mode: the same bots run *alongside* a real field run
              (`run_field.py`, same --swarm), adding load while the real agents are
              actually building something. Use this to raise contention density for
              the video, or to stress-test a swarm under realistic conditions.

Adaptive rate (AIMD-shaped)
----------------------------
Each bot tracks its own claim interval. A denial backs it off (multiply the
interval up, within --max-interval); a grant makes it bolder (multiply the
interval down, within --min-interval). Three consequences, all deliberate:

  - It is self-limiting under load: a contested swarm sees each bot's request rate
    fall automatically, which is kinder to the cluster's Request Unit budget than a
    fixed-rate hammer.
  - It behaves like a plausible competitor rather than a blunt load generator --
    closer to what a real, courteous agent would do than a tight retry loop.
  - It is fair: an unlucky bot backs off instead of indefinitely crowding a
    resource it keeps losing.

Every attempt's (timestamp, interval, outcome) is recorded; --log-interval-csv
writes it out, and it is the source for the "the rate visibly breathes with
contention" chart used in the project's demo video.

Release is not optional
------------------------
A won claim is always released -- via try/finally, including on Ctrl-C or an
unexpected exception during the simulated hold -- because a bot that never lets go
would starve the very field run it is supposed to be stress-testing alongside, not
prove anything about contention. The hold time is deliberately short and must stay
well under --ttl; see `_validate_args` for the enforced margin.

Cost brake
----------
CockroachDB Basic bills Request Units, and this cluster carries a monthly cap (see
`C:\\_Local_DEV\\CREDENTIALS\\cockroachdb\\cluster.md`, not part of this repository).
--max-attempts-total and --time-limit are hard ceilings, on by default; a plan that
would exceed either is refused unless you pass --i-have-checked-the-ru-budget. The
printed RU estimate is a labelled planning number (see `estimate_ru_cost`), not a
measurement -- CockroachDB's own console has the real figure.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
SRC_DIR = REPO_ROOT / "src"
for _p in (SRC_DIR, HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

DEFAULT_BOTS = 3
DEFAULT_TASK_PREFIX = "storm:task:"
DEFAULT_DRY_RUN_TASK_PREFIX = "dryrun:task:"
DEFAULT_TASK_COUNT = 2
DEFAULT_TTL_SECONDS = 30
DEFAULT_HOLD_SECONDS = 3.0
DEFAULT_START_INTERVAL = 1.0
DEFAULT_MIN_INTERVAL = 0.25
DEFAULT_MAX_INTERVAL = 20.0
DEFAULT_BACKOFF_FACTOR = 1.6  # denial: interval *= this (grows)
DEFAULT_BOLDNESS_FACTOR = 0.7  # grant: interval *= this (shrinks)
DEFAULT_MAX_ATTEMPTS_PER_BOT = 40
DEFAULT_MAX_ATTEMPTS_TOTAL = 300
DEFAULT_TIME_LIMIT_SECONDS = 90
DEFAULT_DRY_RUN_TIME_LIMIT_SECONDS = 30

# The one swarm id nothing in this script should ever touch by accident -- the
# real two-machine field run's contended swarm, not a synthetic bot tournament.
PROTECTED_SWARM_ID = "poc-starmap-2026-07-30"

COLOR_GROUP = "bot"  # shared by every BotAgent instance -- see module docstring's
# "Naming and color convention" note in the handoff report: all bots read as one
# recognisable group, distinct from any single named model's own colour.


class UsageError(Exception):
    """A configuration problem the caller can fix -- reported without a traceback."""


# --------------------------------------------------------------------------- naming


def bot_letter(index: int) -> str:
    """0->A, 1->B, ..., 25->Z, 26->AA, 27->AB, ... -- spreadsheet-column style.

    Chosen over zero-padded numbers because "BotAgent A/B/C" is the display
    convention the project settled on; this keeps that convention from running out
    at 26 instances instead of silently switching styles.
    """
    if index < 0:
        raise ValueError(f"index must be >= 0, got {index}")
    letters: list[str] = []
    n = index
    while True:
        n, rem = divmod(n, 26)
        letters.append(chr(ord("A") + rem))
        if n == 0:
            break
        n -= 1
    return "".join(reversed(letters))


@dataclass(frozen=True)
class BotIdentity:
    letter: str
    host_label: str

    @property
    def display_name(self) -> str:
        return f"BotAgent {self.letter}"

    @property
    def agent_id(self) -> str:
        # Same shape as the real field run's ids (see run_field.py): a stable base
        # plus @host_label, so ids stay collision-free across machines too.
        return f"BotAgent-{self.letter}@{self.host_label}"

    @property
    def capabilities(self) -> dict:
        """Stored in the existing `agents.capabilities` JSONB column.

        No schema or contract change: `register_agent(..., capabilities=...)`
        already accepts an arbitrary JSON object (roshambo/memory.py). Carrying the
        human display name and the shared colour-group key here is the additive
        path -- any reader (the demo web UI, this script's own output, the video)
        can pick it up without a migration.
        """
        return {
            "display_name": self.display_name,
            "color_group": COLOR_GROUP,
            "role": "protocol-conformance-simulator",
        }


# ---------------------------------------------------------------------------- rate


@dataclass
class AimdRate:
    """Additive-identity, multiplicative-in/decrease interval control.

    Not literally TCP's AIMD (that shrinks a *window* multiplicatively on loss and
    grows it additively on success); the shape asked for here is the mirror image
    in *interval* terms -- a denial should make the bot markedly more cautious
    right away, a grant should make it only somewhat bolder -- so both directions
    are multiplicative, with distinct factors and hard clamps. The asymmetry
    (backoff_factor > 1 / boldness_factor closer to 1) is what makes it back off
    fast and recover slowly, matching a "polite competitor" rather than a
    hair-trigger one.
    """

    interval: float
    min_interval: float
    max_interval: float
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR
    boldness_factor: float = DEFAULT_BOLDNESS_FACTOR

    def on_denied(self) -> None:
        self.interval = min(self.max_interval, self.interval * self.backoff_factor)

    def on_granted(self) -> None:
        self.interval = max(self.min_interval, self.interval * self.boldness_factor)


@dataclass
class IntervalEvent:
    timestamp: str
    agent_id: str
    resource: str
    outcome: str  # "granted" | "denied" | "error"
    interval_before: float
    interval_after: float


# ------------------------------------------------------------------------- records


@dataclass
class AttemptRecord:
    agent_id: str
    resource: str
    outcome: str  # "granted" | "denied" | "error"
    claim_id: str | None
    attempt_started: float
    held_from: float | None = None  # perf_counter right after claim() returns granted
    held_until: float | None = None  # perf_counter right before release() is called
    error: str | None = None


@dataclass
class BotReport:
    identity: BotIdentity
    attempts: list[AttemptRecord] = field(default_factory=list)
    interval_events: list[IntervalEvent] = field(default_factory=list)

    @property
    def granted(self) -> list[AttemptRecord]:
        return [a for a in self.attempts if a.outcome == "granted"]

    @property
    def denied(self) -> list[AttemptRecord]:
        return [a for a in self.attempts if a.outcome == "denied"]

    @property
    def errored(self) -> list[AttemptRecord]:
        return [a for a in self.attempts if a.outcome == "error"]


@dataclass
class RunReport:
    mode: str  # "dry-run" | "storm"
    swarm_id: str
    bots: list[BotReport] = field(default_factory=list)
    two_simultaneous_holders_detected: bool = False
    overlap_violations: list[tuple[str, str, str]] = field(default_factory=list)
    unreleased_claims: list[tuple[str, str]] = field(default_factory=list)  # (agent_id, claim_id)

    @property
    def total_attempts(self) -> int:
        return sum(len(b.attempts) for b in self.bots)

    @property
    def total_granted(self) -> int:
        return sum(len(b.granted) for b in self.bots)

    @property
    def total_denied(self) -> int:
        return sum(len(b.denied) for b in self.bots)

    @property
    def total_errored(self) -> int:
        return sum(len(b.errored) for b in self.bots)

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "swarm_id": self.swarm_id,
            "bots": [b.identity.display_name for b in self.bots],
            "total_attempts": self.total_attempts,
            "total_granted": self.total_granted,
            "total_denied": self.total_denied,
            "total_errored": self.total_errored,
            "two_simultaneous_holders_detected": self.two_simultaneous_holders_detected,
            "overlap_violations": self.overlap_violations,
            "unreleased_claims": self.unreleased_claims,
            "per_bot": [
                {
                    "agent_id": b.identity.agent_id,
                    "display_name": b.identity.display_name,
                    "attempts": len(b.attempts),
                    "granted": len(b.granted),
                    "denied": len(b.denied),
                    "errored": len(b.errored),
                    "final_interval": b.interval_events[-1].interval_after if b.interval_events else None,
                }
                for b in self.bots
            ],
            "interval_log": [
                {
                    "timestamp": e.timestamp,
                    "agent_id": e.agent_id,
                    "resource": e.resource,
                    "outcome": e.outcome,
                    "interval_before": e.interval_before,
                    "interval_after": e.interval_after,
                }
                for b in self.bots
                for e in b.interval_events
            ],
        }


def _windows_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _finalize(report: RunReport) -> RunReport:
    """The one property that matters, checked from client-observed timing -- with a
    window narrow enough not to indict the network for what it didn't do.

    A first version of this check used [just-before-claim(), just-after-release()]
    as the window -- including both round trips to a Cloud cluster. Verified
    against a real dry-run: it flagged an "overlap" that the server-side
    `audit_log` (created_at-ordered, PROTOCOL.md's own source of truth) proved was
    never real -- every claim on the resource was correctly granted to, and denied
    as held by, exactly the actual live holder, in strict sequence. The RTT padding
    on both ends of the client-side window was enough, at real network latency, to
    make two genuinely sequential holds look concurrent.

    The fix: use [held_from, held_until] -- held_from is captured the instant
    claim() *returns* a grant (so the server had already committed it), held_until
    is captured the instant *before* release() is called (so we know we still held
    it at least that long). Both round trips are excluded. This makes the window a
    conservative *subset* of the true holding period, so it can still miss a
    violation at the very margins, but it cannot manufacture one out of network
    latency -- the failure mode that actually occurred needed fixing, not
    tolerating. The database's own audit_log remains the final authority; this is
    corroborating evidence from the client side, not a replacement for it.
    """
    by_resource: dict[str, list[tuple[str, float, float]]] = {}
    for bot in report.bots:
        for a in bot.attempts:
            if a.outcome != "granted":
                continue
            if a.held_from is None or a.held_until is None:
                report.unreleased_claims.append((a.agent_id, a.claim_id or "?"))
                continue
            by_resource.setdefault(a.resource, []).append((a.claim_id or "?", a.held_from, a.held_until))

    for resource, windows in by_resource.items():
        for i in range(len(windows)):
            for j in range(i + 1, len(windows)):
                x, y = windows[i], windows[j]
                if _windows_overlap((x[1], x[2]), (y[1], y[2])):
                    report.two_simultaneous_holders_detected = True
                    report.overlap_violations.append((resource, x[0], y[0]))

    return report


# ---------------------------------------------------------------------- RU costing


def estimate_ru_cost(total_attempts: int, expected_grants: int) -> dict:
    """A labelled planning estimate -- see the module docstring's Cost brake note.

    Basis: one attempt is one claim upsert + one audit insert
    (`roshambo.memory.Roshambo.claim` / `_audit`); a grant adds one release write +
    its audit insert. 5 RU/write is a conservative round number, not a
    vendor-confirmed figure.
    """
    writes = total_attempts * 2 + expected_grants * 2
    ru_per_write = 5
    return {
        "attempts": total_attempts,
        "expected_grants": expected_grants,
        "estimated_writes": writes,
        "ru_per_write_estimate": ru_per_write,
        "estimated_ru_total": writes * ru_per_write,
        "basis": (
            "planning estimate only: 2 writes/attempt (claim + audit) + 2 writes per "
            "grant (release + audit), 5 RU/write as a conservative round number -- "
            "not read from CockroachDB's own RU accounting"
        ),
    }


# --------------------------------------------------------------------------- sleep


def _interruptible_sleep(seconds: float, stop_event: threading.Event, chunk: float = 0.2) -> None:
    """Sleep in small slices so a set stop_event (Ctrl-C, or a caller-driven abort)
    is honoured within `chunk` seconds instead of after the full duration -- this is
    what lets a held claim's hold-and-release still happen promptly on interrupt."""
    remaining = seconds
    while remaining > 0 and not stop_event.is_set():
        step = min(chunk, remaining)
        time.sleep(step)
        remaining -= step


# ----------------------------------------------------------------------- bot loop


def bot_loop(
    client_factory,
    identity: BotIdentity,
    tasks: tuple[str, ...],
    rate: AimdRate,
    ttl_seconds: int,
    hold_seconds: float,
    max_attempts: int,
    deadline: float,
    stop_event: threading.Event,
    report: BotReport,
) -> None:
    """One bot's whole life: register once, then attempt/hold/release until a cap.

    `client_factory()` must return a fresh, unconnected object exposing
    `.register_agent(framework, host, capabilities, agent_id)`, `.conn`,
    `.claim(resource, agent_id, intent, ttl_seconds)`, `.release(claim_id)` and
    `.close()` -- the `roshambo.memory.Roshambo` surface. Kept duck-typed so the
    offline tests can supply a fake without a cluster.
    """
    from roshambo.models import Claim

    client = client_factory()
    try:
        _ = client.conn
        client.register_agent(
            framework="bot",
            host=identity.host_label,
            capabilities=identity.capabilities,
            agent_id=identity.agent_id,
        )

        attempts = 0
        while attempts < max_attempts and time.monotonic() < deadline and not stop_event.is_set():
            resource = tasks[attempts % len(tasks)]
            interval_before = rate.interval
            try:
                attempt_started = time.perf_counter()
                result = client.claim(
                    resource, identity.agent_id, f"{identity.display_name} contention probe", ttl_seconds
                )
                # Captured the instant claim() *returns* -- the server had already
                # committed the grant by then, so this excludes the request's own
                # round trip from the "held" window (see _finalize's docstring).
                held_from = time.perf_counter()
                granted = isinstance(result, Claim)
                record = AttemptRecord(
                    agent_id=identity.agent_id,
                    resource=resource,
                    outcome="granted" if granted else "denied",
                    claim_id=result.claim_id if granted else None,
                    attempt_started=attempt_started,
                    held_from=held_from if granted else None,
                )
                report.attempts.append(record)

                if granted:
                    try:
                        _interruptible_sleep(hold_seconds, stop_event)
                    finally:
                        # Captured *before* release() is called -- the last instant
                        # we know for certain we still held it, excluding the
                        # release call's own round trip on this end too.
                        record.held_until = time.perf_counter()
                        # RELEASE-PFLICHT: always, even if the hold above raised or
                        # was interrupted. A won claim that is never handed back
                        # would starve real work sharing this swarm.
                        client.release(result.claim_id)
                    rate.on_granted()
                else:
                    rate.on_denied()

                report.interval_events.append(
                    IntervalEvent(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        agent_id=identity.agent_id,
                        resource=resource,
                        outcome=record.outcome,
                        interval_before=interval_before,
                        interval_after=rate.interval,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - one bad attempt must not kill the bot
                report.attempts.append(
                    AttemptRecord(
                        agent_id=identity.agent_id,
                        resource=resource,
                        outcome="error",
                        claim_id=None,
                        attempt_started=time.perf_counter(),
                        error=str(exc),
                    )
                )
                print(f"[{identity.display_name}] attempt error (continuing): {exc}", file=sys.stderr)

            attempts += 1
            if attempts < max_attempts and time.monotonic() < deadline:
                _interruptible_sleep(rate.interval, stop_event)
    finally:
        client.close()


# --------------------------------------------------------------------- real client


def _real_client_factory(cfg):
    from roshambo.memory import PlaceholderEmbedder, Roshambo

    def factory():
        return Roshambo(cfg, embedder=PlaceholderEmbedder(dim=cfg.embedding_dim))

    return factory


def _load_cfg(swarm_id: str, ttl_seconds: int):
    from rsb import resolve_dsn  # local import: rsb.py lives next to this script
    from roshambo.config import RoshamboConfig
    import os

    dsn = resolve_dsn(dict(os.environ))
    return RoshamboConfig(dsn=dsn, swarm_id=swarm_id, lease_ttl_seconds=ttl_seconds)


# ------------------------------------------------------------------------- runner


def run_tournament(
    client_factory,
    mode: str,
    swarm_id: str,
    identities: list[BotIdentity],
    tasks: tuple[str, ...],
    make_rate,
    ttl_seconds: int,
    hold_seconds: float,
    max_attempts_per_bot: int,
    time_limit_seconds: int,
    stop_event: threading.Event | None = None,
) -> RunReport:
    stop_event = stop_event or threading.Event()
    report = RunReport(mode=mode, swarm_id=swarm_id)
    deadline = time.monotonic() + time_limit_seconds
    threads = []

    for identity in identities:
        bot_report = BotReport(identity=identity)
        report.bots.append(bot_report)
        rate = make_rate()
        t = threading.Thread(
            target=bot_loop,
            args=(
                client_factory,
                identity,
                tasks,
                rate,
                ttl_seconds,
                hold_seconds,
                max_attempts_per_bot,
                deadline,
                stop_event,
                bot_report,
            ),
            name=identity.display_name,
        )
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=time_limit_seconds + 30)  # grace period beyond the deadline

    return _finalize(report)


# ------------------------------------------------------------------------------ CLI


def _validate_args(args: argparse.Namespace) -> None:
    if args.bots < 1:
        raise UsageError(f"--bots must be >= 1, got {args.bots}")
    if args.hold_seconds <= 0:
        raise UsageError(f"--hold-seconds must be > 0, got {args.hold_seconds}")
    if args.ttl <= args.hold_seconds * 2:
        raise UsageError(
            f"--ttl ({args.ttl}s) must leave a comfortable margin over --hold-seconds "
            f"({args.hold_seconds}s) -- otherwise this measures lease expiry, not "
            "contention. Use --ttl of at least a few times --hold-seconds."
        )
    if args.min_interval <= 0:
        raise UsageError(f"--min-interval must be > 0, got {args.min_interval}")
    if args.max_interval < args.min_interval:
        raise UsageError("--max-interval must be >= --min-interval")
    if args.max_attempts_per_bot < 1:
        raise UsageError("--max-attempts-per-bot must be >= 1")
    total = args.bots * args.max_attempts_per_bot
    if total > args.max_attempts_total and not args.i_have_checked_the_ru_budget:
        raise UsageError(
            f"planned {total} attempts ({args.bots} bots x {args.max_attempts_per_bot} "
            f"attempts each) exceeds --max-attempts-total {args.max_attempts_total}. "
            "Lower --bots/--max-attempts-per-bot, raise --max-attempts-total only "
            "after checking the RU graph, or pass --i-have-checked-the-ru-budget."
        )
    if (
        args.dry_run
        and args.swarm == PROTECTED_SWARM_ID
        and not args.i_have_checked_the_ru_budget
    ):
        raise UsageError(
            f"--dry-run against '{PROTECTED_SWARM_ID}' is refused: that is the real "
            "two-machine field run's swarm, and a dry-run tournament has no business "
            "contending on it. Omit --swarm to get a fresh auto-generated one, pick "
            "a different --swarm, or pass --i-have-checked-the-ru-budget if this is "
            "genuinely intended."
        )


def build_identities(bots: int, host_label: str) -> list[BotIdentity]:
    return [BotIdentity(letter=bot_letter(i), host_label=host_label) for i in range(bots)]


def build_tasks(args: argparse.Namespace) -> tuple[str, ...]:
    if args.tasks:
        return tuple(t.strip() for t in args.tasks.split(",") if t.strip())
    prefix = args.task_prefix or (DEFAULT_DRY_RUN_TASK_PREFIX if args.dry_run else DEFAULT_TASK_PREFIX)
    return tuple(f"{prefix}{i:02d}" for i in range(1, args.task_count + 1))


def _default_dry_run_swarm() -> str:
    return f"bot-dryrun-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"


def _print_summary(report: RunReport, estimate: dict) -> None:
    header = "TOURNAMENT RESULT" if report.mode == "dry-run" else "STORM RESULT"
    print(f"\n=== {header} ({report.swarm_id}) ===", file=sys.stderr)
    print(
        f"bots: {', '.join(b.identity.display_name for b in report.bots)}",
        file=sys.stderr,
    )
    for b in report.bots:
        final_interval = b.interval_events[-1].interval_after if b.interval_events else None
        print(
            f"  {b.identity.display_name:<12} attempts={len(b.attempts):<4} "
            f"granted={len(b.granted):<4} denied={len(b.denied):<4} "
            f"errored={len(b.errored):<3} final_interval={final_interval:.2f}s"
            if final_interval is not None
            else f"  {b.identity.display_name:<12} attempts=0",
            file=sys.stderr,
        )
    print(
        f"total: {report.total_attempts} attempts, {report.total_granted} granted, "
        f"{report.total_denied} denied, {report.total_errored} errored",
        file=sys.stderr,
    )
    print(
        "two simultaneous holders of the same task, at any point: "
        f"{'YES -- CONTRACT VIOLATION' if report.two_simultaneous_holders_detected else 'no'}",
        file=sys.stderr,
    )
    if report.unreleased_claims:
        print(
            f"WARNING: {len(report.unreleased_claims)} claim(s) never observed a "
            "release (thread join may have timed out): "
            f"{report.unreleased_claims}",
            file=sys.stderr,
        )
    print(
        f"RU estimate (planning only, not measured): ~{estimate['estimated_ru_total']} RU",
        file=sys.stderr,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bot_agent.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # The judge-friendly proof: 3 bots, only against each other, in well\n"
            "  # under a minute, no LLM keys required:\n"
            "  python bot_agent.py --dry-run --bots 3\n\n"
            "  # Extra contention alongside a real field run on the same swarm:\n"
            "  python bot_agent.py --swarm poc-starmap-2026-07-30 --bots 3 "
            "--tasks starmap:task:01,starmap:task:02\n"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "tournament mode: bots compete only against each other, on their own "
            "swarm (auto-generated unless --swarm is given). No LLM agents, no "
            "files, no field run involved."
        ),
    )
    parser.add_argument(
        "--swarm",
        default=None,
        help=(
            "ROSHAMBO_SWARM_ID to run against. Required for storm mode; optional "
            "for --dry-run (a fresh 'bot-dryrun-<timestamp>' swarm is used if omitted)."
        ),
    )
    parser.add_argument("--bots", type=int, default=DEFAULT_BOTS, help="number of parallel BotAgent instances")
    parser.add_argument(
        "--host-label",
        default=None,
        help="stable label for this machine, used in every bot's agent id (default: local hostname)",
    )
    parser.add_argument("--tasks", default=None, help="comma-separated resource names (overrides --task-prefix/-count)")
    parser.add_argument("--task-prefix", default=None, help="default: 'dryrun:task:' in --dry-run, else 'storm:task:'")
    parser.add_argument("--task-count", type=int, default=DEFAULT_TASK_COUNT)
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL_SECONDS, help="lease seconds per claim")
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=DEFAULT_HOLD_SECONDS,
        help="simulated work duration after a successful claim (must stay well under --ttl)",
    )
    parser.add_argument("--start-interval", type=float, default=DEFAULT_START_INTERVAL)
    parser.add_argument("--min-interval", type=float, default=DEFAULT_MIN_INTERVAL)
    parser.add_argument("--max-interval", type=float, default=DEFAULT_MAX_INTERVAL)
    parser.add_argument("--backoff-factor", type=float, default=DEFAULT_BACKOFF_FACTOR, help="interval *= this on denial")
    parser.add_argument("--boldness-factor", type=float, default=DEFAULT_BOLDNESS_FACTOR, help="interval *= this on grant")
    parser.add_argument("--max-attempts-per-bot", type=int, default=None)
    parser.add_argument("--max-attempts-total", type=int, default=DEFAULT_MAX_ATTEMPTS_TOTAL)
    parser.add_argument("--time-limit", type=int, default=None, help="wall-clock seconds, hard cap")
    parser.add_argument(
        "--i-have-checked-the-ru-budget",
        action="store_true",
        help="required to exceed --max-attempts-total, or to --dry-run against the protected swarm",
    )
    parser.add_argument("--log-interval-csv", default=None, help="write the (timestamp, interval, outcome) log here")
    parser.add_argument("--json", action="store_true", help="also print the final report as JSON on stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.max_attempts_per_bot is None:
        args.max_attempts_per_bot = 10 if args.dry_run else DEFAULT_MAX_ATTEMPTS_PER_BOT
    if args.time_limit is None:
        args.time_limit = DEFAULT_DRY_RUN_TIME_LIMIT_SECONDS if args.dry_run else DEFAULT_TIME_LIMIT_SECONDS
    if args.swarm is None:
        if not args.dry_run:
            parser.error("--swarm is required outside --dry-run")
        args.swarm = _default_dry_run_swarm()

    try:
        _validate_args(args)
    except UsageError as exc:
        print(f"bot_agent.py: {exc}", file=sys.stderr)
        return 2

    host_label = args.host_label or socket.gethostname()
    identities = build_identities(args.bots, host_label)
    tasks = build_tasks(args)

    total_attempts = args.bots * args.max_attempts_per_bot
    expected_grants = args.bots * args.max_attempts_per_bot  # worst case: every attempt granted
    estimate = estimate_ru_cost(total_attempts, expected_grants)
    print(
        f"plan: {'dry-run tournament' if args.dry_run else 'storm'} on swarm '{args.swarm}', "
        f"{args.bots} bots ({', '.join(i.display_name for i in identities)}) x up to "
        f"{args.max_attempts_per_bot} attempts each, {len(tasks)} task(s): {', '.join(tasks)}",
        file=sys.stderr,
    )
    print(
        f"RU estimate (planning only, worst case): ~{estimate['estimated_ru_total']} RU",
        file=sys.stderr,
    )

    cfg = _load_cfg(args.swarm, args.ttl)
    client_factory = _real_client_factory(cfg)

    def make_rate() -> AimdRate:
        return AimdRate(
            interval=args.start_interval,
            min_interval=args.min_interval,
            max_interval=args.max_interval,
            backoff_factor=args.backoff_factor,
            boldness_factor=args.boldness_factor,
        )

    stop_event = threading.Event()

    def _handle_sigint(_signum, _frame):
        print("\nbot_agent.py: interrupted -- signalling bots to finish releasing and stop", file=sys.stderr)
        stop_event.set()

    import signal

    previous_handler = signal.signal(signal.SIGINT, _handle_sigint)
    try:
        report = run_tournament(
            client_factory=client_factory,
            mode="dry-run" if args.dry_run else "storm",
            swarm_id=args.swarm,
            identities=identities,
            tasks=tasks,
            make_rate=make_rate,
            ttl_seconds=args.ttl,
            hold_seconds=args.hold_seconds,
            max_attempts_per_bot=args.max_attempts_per_bot,
            time_limit_seconds=args.time_limit,
            stop_event=stop_event,
        )
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    _print_summary(report, estimate)

    if args.log_interval_csv:
        _write_interval_csv(report, Path(args.log_interval_csv))
        print(f"interval log written to {args.log_interval_csv}", file=sys.stderr)

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))

    return 1 if report.two_simultaneous_holders_detected else 0


def _write_interval_csv(report: RunReport, path: Path) -> None:
    import csv

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp", "agent_id", "resource", "outcome", "interval_before", "interval_after"])
        for bot in report.bots:
            for e in bot.interval_events:
                writer.writerow([e.timestamp, e.agent_id, e.resource, e.outcome, e.interval_before, e.interval_after])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UsageError as exc:  # pragma: no cover - defensive, _validate_args covers the normal path
        print(f"bot_agent.py: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
