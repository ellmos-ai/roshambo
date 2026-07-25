"""Acceptance criterion 1: 20 concurrent claims on one resource, exactly one winner.

Nothing here is simulated. Every worker is a separate OS thread with its own psycopg
connection to a real CockroachDB node, and they are released from a barrier so the
statements genuinely overlap. A test that serialised the workers, shared one connection,
or mocked the database would prove nothing about the property under test.
"""

from __future__ import annotations

import multiprocessing
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import pytest

from roshambo.config import RoshamboConfig
from roshambo.memory import PlaceholderEmbedder, Roshambo
from roshambo.models import Claim, ClaimDenied

pytestmark = pytest.mark.live

CONTENDERS = 20


def _contend(cfg: RoshamboConfig, resource: str, agent_id: str, barrier: threading.Barrier):
    """One agent: own connection, wait at the barrier, then claim."""
    with Roshambo(cfg, embedder=PlaceholderEmbedder(dim=cfg.embedding_dim)) as client:
        # Connect before the barrier so the TCP handshake is not part of the race —
        # otherwise the workers would be staggered by connection setup and the
        # statements would barely overlap.
        _ = client.conn
        barrier.wait(timeout=60)
        return client.claim(resource, agent_id, f"work planned by {agent_id}")


def _run_contest(cfg: RoshamboConfig, resource: str, contenders: int = CONTENDERS):
    barrier = threading.Barrier(contenders)
    with ThreadPoolExecutor(max_workers=contenders) as pool:
        futures = [
            pool.submit(_contend, cfg, resource, f"agent-{i:02d}", barrier)
            for i in range(contenders)
        ]
        return [f.result() for f in futures]


def _contend_timed(cfg: RoshamboConfig, resource: str, agent_id: str, barrier: threading.Barrier):
    """Same as `_contend`, but records when the claim statement was in flight."""
    with Roshambo(cfg, embedder=PlaceholderEmbedder(dim=cfg.embedding_dim)) as client:
        _ = client.conn
        barrier.wait(timeout=60)
        started = time.perf_counter()
        result = client.claim(resource, agent_id, f"work planned by {agent_id}")
        return result, started, time.perf_counter()


def _peak_overlap(intervals: list[tuple[float, float]]) -> int:
    """How many claim statements were in flight at the busiest instant."""
    events = [(start, 1) for start, _ in intervals] + [(end, -1) for _, end in intervals]
    events.sort(key=lambda e: (e[0], e[1]))
    current = peak = 0
    for _timestamp, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def test_twenty_concurrent_claims_produce_exactly_one_winner(cfg: RoshamboConfig):
    results = _run_contest(cfg, "repo:demo:contended.py")

    granted = [r for r in results if isinstance(r, Claim)]
    denied = [r for r in results if isinstance(r, ClaimDenied)]

    assert len(results) == CONTENDERS
    assert len(granted) == 1, f"expected exactly 1 winner, got {len(granted)}"
    assert len(denied) == CONTENDERS - 1


def test_the_claims_really_did_overlap_in_time(cfg: RoshamboConfig, record_property):
    """Checks the premise of every other test in this file.

    "Exactly one winner" is also what a strictly serial run produces, so on its own it
    cannot distinguish a real race from twenty polite turns. This measures when each
    claim statement was actually in flight and reports the peak overlap, so the
    single-winner result above is known to have been decided under contention rather
    than by arrival order.

    The floor is deliberately well below `CONTENDERS`: a loaded CI machine may stagger a
    few threads, and this test should fail when the workers were serialised, not when
    the scheduler was untidy. The measured peak is recorded as a test property so the
    real number — not the floor — is what lands in the evidence file.
    """
    barrier = threading.Barrier(CONTENDERS)
    with ThreadPoolExecutor(max_workers=CONTENDERS) as pool:
        futures = [
            pool.submit(
                _contend_timed, cfg, "repo:demo:contended-timed.py", f"agent-{i:02d}", barrier
            )
            for i in range(CONTENDERS)
        ]
        outcomes = [f.result() for f in futures]

    results = [r for r, _, _ in outcomes]
    peak = _peak_overlap([(start, end) for _, start, end in outcomes])
    record_property("peak_concurrent_claims", peak)
    record_property("contenders", CONTENDERS)

    assert len([r for r in results if isinstance(r, Claim)]) == 1
    assert peak >= 10, (
        f"only {peak} of {CONTENDERS} claim statements were ever in flight at once — "
        "the workers were effectively serialised, so this run does not demonstrate "
        "the concurrency property"
    )


def test_every_loser_is_told_who_won(cfg: RoshamboConfig):
    """The 19 losers must all name the same holder — the one that actually won.

    A denial that named a different or stale holder would send agents chasing a lease
    that does not exist.
    """
    results = _run_contest(cfg, "repo:demo:contended-2.py")

    granted = [r for r in results if isinstance(r, Claim)]
    denied = [r for r in results if isinstance(r, ClaimDenied)]
    assert len(granted) == 1

    winner = granted[0]
    assert {d.held_by for d in denied} == {winner.agent_id}
    assert all(d.intent == winner.intent for d in denied)


def test_the_winner_is_the_one_recorded_in_the_database(cfg: RoshamboConfig):
    results = _run_contest(cfg, "repo:demo:contended-3.py")
    winner = next(r for r in results if isinstance(r, Claim))

    with Roshambo(cfg, embedder=PlaceholderEmbedder(dim=cfg.embedding_dim)) as observer:
        held = observer.who_has("repo:demo:contended-3.py")

    assert held is not None
    assert held.claim_id == winner.claim_id
    assert held.agent_id == winner.agent_id


def test_concurrent_claims_on_distinct_resources_all_succeed(cfg: RoshamboConfig):
    """Sanity check on the other side: the lease must not serialise unrelated work."""
    barrier = threading.Barrier(CONTENDERS)
    with ThreadPoolExecutor(max_workers=CONTENDERS) as pool:
        futures = [
            pool.submit(_contend, cfg, f"repo:demo:file-{i:02d}.py", f"agent-{i:02d}", barrier)
            for i in range(CONTENDERS)
        ]
        results = [f.result() for f in futures]

    assert all(isinstance(r, Claim) for r in results)


def contend_in_process(args: tuple) -> tuple[bool, str, str]:
    """Worker body for the multi-process contest. Module level so it can be pickled.

    Returns plain strings rather than model objects: what crosses the process boundary
    should not depend on those staying picklable.
    """
    cfg, resource, agent_id, barrier = args
    with Roshambo(cfg, embedder=PlaceholderEmbedder(dim=cfg.embedding_dim)) as client:
        _ = client.conn
        barrier.wait(timeout=120)
        result = client.claim(resource, agent_id, f"work planned by {agent_id}")
        if isinstance(result, Claim):
            return True, result.claim_id, result.agent_id
        return False, "", result.held_by


def test_twenty_separate_processes_also_produce_exactly_one_winner(cfg: RoshamboConfig):
    """The same criterion without a shared interpreter.

    Threads already contend for real — psycopg releases the GIL around socket I/O, and
    the measured peak overlap is the whole set — but "it is only threads" is the obvious
    objection to a single-winner claim, and it costs one test to remove it. Twenty OS
    processes, twenty interpreters, twenty connections, one barrier, one winner.

    This is the closest the suite gets to the real deployment shape, where the agents are
    not even on the same machine.

    Note for anyone timing the suite: this is the slow test, roughly 28 s of the core
    suite's runtime, almost all of it Windows process spawn. It is left unmarked and
    always runs because it is acceptance evidence, not an optional extra.
    """
    resource = "repo:demo:contended-processes.py"
    with multiprocessing.Manager() as manager:
        barrier = manager.Barrier(CONTENDERS)
        payload = [(cfg, resource, f"proc-agent-{i:02d}", barrier) for i in range(CONTENDERS)]
        with ProcessPoolExecutor(max_workers=CONTENDERS) as pool:
            outcomes = list(pool.map(contend_in_process, payload))

    winners = [(cid, agent) for granted, cid, agent in outcomes if granted]
    losers = [holder for granted, _, holder in outcomes if not granted]

    assert len(outcomes) == CONTENDERS
    assert len(winners) == 1, f"expected exactly 1 winner across processes, got {len(winners)}"
    assert set(losers) == {winners[0][1]}


def test_takeover_of_an_expired_lease_is_also_single_winner(cfg: RoshamboConfig):
    """The riskiest path is takeover, not first acquisition.

    On takeover the ON CONFLICT branch runs in all 20 transactions at once and each of
    them sees an expired row. Exactly one may still win.
    """
    resource = "repo:demo:contended-expired.py"
    with Roshambo(cfg, embedder=PlaceholderEmbedder(dim=cfg.embedding_dim)) as seed:
        first = seed.claim(resource, "agent-crashed", "abandoned work", 1)
        assert isinstance(first, Claim)

    time.sleep(1.5)

    results = _run_contest(cfg, resource)
    granted = [r for r in results if isinstance(r, Claim)]
    assert len(granted) == 1, f"expected exactly 1 winner on takeover, got {len(granted)}"
    assert granted[0].claim_id != first.claim_id
