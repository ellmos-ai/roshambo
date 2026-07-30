"""Offline tests for demo/multivendor/contention_storm.py -- no cluster needed.

Two things are worth proving without a database: the cost-brake arithmetic (so a
misconfigured --workers/--waves cannot silently blow the RU budget), and the
"two simultaneous holders" detection logic itself -- using fake, in-memory claim
stores so the detector's correctness does not depend on the real cluster ever
misbehaving. A store that (correctly) serialises grants must come back clean; a
store rigged to double-grant (simulating a hypothetical contract violation) must be
caught by both the per-wave and the global check. That is what would tell us the
detector has a blind spot, which is the only thing worth testing without paying for
cluster time.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
DEMO_DIR = REPO_ROOT / "demo" / "multivendor"
for p in (SRC, DEMO_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from roshambo.models import Claim, ClaimDenied  # noqa: E402

import contention_storm as storm  # noqa: E402


# --------------------------------------------------------------------------- planning


def test_default_tasks_generates_expected_names():
    tasks = storm.default_tasks("storm:task:", 3)
    assert tasks == ("storm:task:01", "storm:task:02", "storm:task:03")


def test_plan_total_attempts_is_the_product():
    plan = storm.StormPlan(
        workers=8, waves=5, tasks=("a", "b", "c"), ttl_seconds=30, pause_between_waves=1.0
    )
    assert plan.total_attempts == 8 * 5 * 3


def test_enforce_caps_allows_a_plan_within_both_ceilings():
    plan = storm.StormPlan(
        workers=4, waves=2, tasks=("a",), ttl_seconds=10, pause_between_waves=0.5
    )
    # Should not raise.
    storm.enforce_caps(plan, max_attempts=100, time_limit_seconds=60)


def test_enforce_caps_refuses_a_plan_over_max_attempts():
    plan = storm.StormPlan(
        workers=100, waves=100, tasks=("a", "b"), ttl_seconds=10, pause_between_waves=0.0
    )
    with pytest.raises(SystemExit):
        storm.enforce_caps(plan, max_attempts=300, time_limit_seconds=99999)


def test_enforce_caps_refuses_a_plan_over_time_limit():
    plan = storm.StormPlan(
        workers=2, waves=1000, tasks=("a",), ttl_seconds=10, pause_between_waves=1.0
    )
    with pytest.raises(SystemExit):
        storm.enforce_caps(plan, max_attempts=999999, time_limit_seconds=60)


def test_enforce_caps_override_permits_an_over_budget_plan():
    plan = storm.StormPlan(
        workers=100, waves=100, tasks=("a", "b"), ttl_seconds=10, pause_between_waves=0.0
    )
    # Should not raise when explicitly overridden.
    storm.enforce_caps(plan, max_attempts=1, time_limit_seconds=1, override=True)


def test_estimate_ru_cost_is_labelled_as_an_estimate_and_scales_with_attempts():
    small = storm.StormPlan(workers=2, waves=1, tasks=("a",), ttl_seconds=10, pause_between_waves=0)
    big = storm.StormPlan(workers=8, waves=5, tasks=("a", "b", "c"), ttl_seconds=10, pause_between_waves=0)

    small_est = storm.estimate_ru_cost(small)
    big_est = storm.estimate_ru_cost(big)

    assert "estimate" in small_est["basis"]
    assert small_est["estimated_ru_total"] > 0
    assert big_est["estimated_ru_total"] > small_est["estimated_ru_total"]
    assert big_est["attempts"] == big.total_attempts


def test_build_plan_prefers_explicit_tasks_over_prefix(monkeypatch):
    import argparse

    args = argparse.Namespace(
        tasks="x:1, x:2",
        task_prefix="unused:",
        task_count=9,
        workers=1,
        waves=1,
        ttl=10,
        pause_between_waves=0.0,
    )
    plan = storm.build_plan(args)
    assert plan.tasks == ("x:1", "x:2")


def test_build_plan_falls_back_to_prefix_and_count():
    import argparse

    args = argparse.Namespace(
        tasks=None,
        task_prefix="s:",
        task_count=2,
        workers=1,
        waves=1,
        ttl=10,
        pause_between_waves=0.0,
    )
    plan = storm.build_plan(args)
    assert plan.tasks == ("s:01", "s:02")


# --------------------------------------------------------------------------- overlap


def test_peak_overlap_of_disjoint_intervals_is_one():
    assert storm._peak_overlap([(0.0, 1.0), (1.1, 2.0), (2.1, 3.0)]) == 1


def test_peak_overlap_of_fully_overlapping_intervals_is_the_count():
    assert storm._peak_overlap([(0.0, 1.0), (0.1, 0.9), (0.2, 0.8)]) == 3


def test_windows_overlap_true_and_false_cases():
    assert storm._windows_overlap((0.0, 1.0), (0.5, 1.5)) is True
    assert storm._windows_overlap((0.0, 1.0), (1.0, 2.0)) is False
    assert storm._windows_overlap((0.0, 1.0), (2.0, 3.0)) is False


# --------------------------------------------------------------------------- fakes


class _FakeStore:
    """A correct, in-process stand-in for the claims table: one holder at a time."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.holders: dict[str, str] = {}
        self._next_id = 0

    def claim(self, resource, agent_id, intent, ttl):
        with self.lock:
            if resource in self.holders:
                return ClaimDenied(
                    resource=resource,
                    held_by="someone-else",
                    intent=intent,
                    expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
                )
            self._next_id += 1
            claim_id = f"fake-{self._next_id}"
            self.holders[resource] = claim_id
            return Claim(
                claim_id=claim_id,
                resource=resource,
                agent_id=agent_id,
                intent=intent,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
            )

    def release(self, claim_id):
        with self.lock:
            for resource, cid in list(self.holders.items()):
                if cid == claim_id:
                    del self.holders[resource]
                    return True
            return False


class _BrokenStore(_FakeStore):
    """Deliberately violates single-winner: every attempt is granted.

    Simulates the hypothetical failure the storm script exists to catch, so the
    detector's correctness is proven against a store that is KNOWN to misbehave, not
    only against one that (like the real one) is expected to behave.
    """

    def claim(self, resource, agent_id, intent, ttl):
        with self.lock:
            self._next_id += 1
            claim_id = f"broken-{self._next_id}"
            self.holders.setdefault(resource, claim_id)
            return Claim(
                claim_id=claim_id,
                resource=resource,
                agent_id=agent_id,
                intent=intent,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
            )

    def release(self, claim_id):
        return True


class _FakeClient:
    def __init__(self, store) -> None:
        self._store = store
        self.conn = object()  # any truthy attribute access is enough for run_wave()

    def claim(self, resource, agent_id, intent, ttl):
        return self._store.claim(resource, agent_id, intent, ttl)

    def release(self, claim_id):
        return self._store.release(claim_id)

    def close(self) -> None:
        pass


def test_run_wave_against_a_correct_store_grants_exactly_one():
    store = _FakeStore()
    result = storm.run_wave(
        client_factory=lambda: _FakeClient(store),
        wave_index=1,
        resource="storm:task:01",
        workers=12,
        ttl_seconds=30,
        agent_prefix="test",
    )
    assert len(result.attempts) == 12
    assert len(result.granted) == 1
    assert result.contract_violation is False
    # The resource must be free again afterwards -- run_wave releases the winner.
    assert "storm:task:01" not in store.holders


def test_run_wave_against_a_broken_store_is_flagged_as_a_violation():
    store = _BrokenStore()
    result = storm.run_wave(
        client_factory=lambda: _FakeClient(store),
        wave_index=1,
        resource="storm:task:01",
        workers=6,
        ttl_seconds=30,
        agent_prefix="test",
    )
    assert len(result.granted) == 6  # every attempt "won" -- the simulated defect
    assert result.contract_violation is True


def test_run_storm_end_to_end_against_a_correct_store_reports_no_violation():
    store = _FakeStore()
    plan = storm.StormPlan(
        workers=5, waves=3, tasks=("t1", "t2"), ttl_seconds=10, pause_between_waves=0.0
    )
    report = storm.run_storm(
        client_factory=lambda: _FakeClient(store),
        plan=plan,
        swarm_id="test-swarm",
        time_limit_seconds=30,
    )
    assert report.total_attempts == plan.total_attempts
    assert report.total_granted == plan.waves * len(plan.tasks)  # exactly one per wave
    assert report.two_simultaneous_holders_detected is False
    assert report.global_overlap_violations == []


def test_run_storm_end_to_end_against_a_broken_store_reports_a_violation():
    store = _BrokenStore()
    plan = storm.StormPlan(
        workers=4, waves=1, tasks=("t1",), ttl_seconds=10, pause_between_waves=0.0
    )
    report = storm.run_storm(
        client_factory=lambda: _FakeClient(store),
        plan=plan,
        swarm_id="test-swarm",
        time_limit_seconds=30,
    )
    assert report.two_simultaneous_holders_detected is True


def test_finalize_detects_global_overlap_across_waves_even_without_a_per_wave_violation():
    """Two separate waves, each individually clean, whose grant windows overlap in
    wall-clock time must still be caught by the cross-wave check."""
    now = time.perf_counter()
    wave_a = storm.WaveResult(
        wave_index=1,
        resource="shared",
        attempts=[],
        granted=[
            storm.AttemptRecord(
                resource="shared", agent_id="a1", granted=True, claim_id="c1",
                started=now, ended=now + 5.0,
            )
        ],
        peak_overlap=1,
        contract_violation=False,
    )
    wave_b = storm.WaveResult(
        wave_index=2,
        resource="shared",
        attempts=[],
        granted=[
            storm.AttemptRecord(
                resource="shared", agent_id="a2", granted=True, claim_id="c2",
                started=now + 2.0, ended=now + 6.0,
            )
        ],
        peak_overlap=1,
        contract_violation=False,
    )
    plan = storm.StormPlan(workers=1, waves=2, tasks=("shared",), ttl_seconds=10, pause_between_waves=0)
    report = storm.StormReport(plan=plan, swarm_id="test-swarm", waves=[wave_a, wave_b])

    finalized = storm._finalize(report)

    assert finalized.two_simultaneous_holders_detected is True
    assert finalized.global_overlap_violations == [("shared", "c1", "c2")]
