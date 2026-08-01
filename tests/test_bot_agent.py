"""Offline tests for demo/multivendor/bot_agent.py -- no cluster needed.

Fakes stand in for the real `roshambo.memory.Roshambo` client so the things worth
proving without paying for cluster time can be proven: the AIMD rate arithmetic,
the naming/identity helpers, argument validation, the release-on-every-path
guarantee (including when the simulated hold raises), and the "two simultaneous
holders" detector itself -- checked against both a store that behaves correctly and
one deliberately rigged to double-grant.
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

import bot_agent  # noqa: E402

from roshambo.models import Claim, ClaimDenied  # noqa: E402

# --------------------------------------------------------------------------- naming


@pytest.mark.parametrize(
    "index,expected",
    [(0, "A"), (1, "B"), (25, "Z"), (26, "AA"), (27, "AB"), (51, "AZ"), (52, "BA")],
)
def test_bot_letter_sequence(index, expected):
    assert bot_agent.bot_letter(index) == expected


def test_bot_letter_rejects_negative():
    with pytest.raises(ValueError):
        bot_agent.bot_letter(-1)


def test_bot_identity_derived_fields():
    identity = bot_agent.BotIdentity(letter="A", host_label="ASUS-GEI")
    assert identity.display_name == "BotAgent A"
    assert identity.agent_id == "BotAgent-A@ASUS-GEI"
    caps = identity.capabilities
    assert caps["display_name"] == "BotAgent A"
    assert caps["color_group"] == bot_agent.COLOR_GROUP
    # every bot shares the same group -- the point of the shared colour convention
    other = bot_agent.BotIdentity(letter="B", host_label="ASUS-GEI")
    assert other.capabilities["color_group"] == caps["color_group"]


def test_build_identities_produces_distinct_collision_free_ids():
    identities = bot_agent.build_identities(4, "ASUS-GEI")
    ids = [i.agent_id for i in identities]
    assert ids == [
        "BotAgent-A@ASUS-GEI",
        "BotAgent-B@ASUS-GEI",
        "BotAgent-C@ASUS-GEI",
        "BotAgent-D@ASUS-GEI",
    ]
    assert len(set(ids)) == len(ids)


# ------------------------------------------------------------------------------ rate


def test_aimd_denial_increases_interval_multiplicatively():
    rate = bot_agent.AimdRate(
        interval=1.0,
        min_interval=0.1,
        max_interval=100.0,
        backoff_factor=2.0,
    )
    rate.on_denied()
    assert rate.interval == pytest.approx(2.0)
    rate.on_denied()
    assert rate.interval == pytest.approx(4.0)


def test_aimd_grant_decreases_interval_multiplicatively():
    rate = bot_agent.AimdRate(
        interval=10.0,
        min_interval=0.1,
        max_interval=100.0,
        boldness_factor=0.5,
    )
    rate.on_granted()
    assert rate.interval == pytest.approx(5.0)
    rate.on_granted()
    assert rate.interval == pytest.approx(2.5)


def test_aimd_interval_clamped_to_max_on_repeated_denial():
    rate = bot_agent.AimdRate(interval=1.0, min_interval=0.1, max_interval=3.0, backoff_factor=2.0)
    for _ in range(10):
        rate.on_denied()
    assert rate.interval == pytest.approx(3.0)


def test_aimd_interval_clamped_to_min_on_repeated_grant():
    rate = bot_agent.AimdRate(
        interval=1.0,
        min_interval=0.2,
        max_interval=100.0,
        boldness_factor=0.5,
    )
    for _ in range(10):
        rate.on_granted()
    assert rate.interval == pytest.approx(0.2)


# --------------------------------------------------------------------------- ru cost


def test_estimate_ru_cost_scales_and_is_labelled():
    small = bot_agent.estimate_ru_cost(total_attempts=5, expected_grants=5)
    big = bot_agent.estimate_ru_cost(total_attempts=50, expected_grants=50)
    assert "estimate" in small["basis"]
    assert big["estimated_ru_total"] > small["estimated_ru_total"]


# ---------------------------------------------------------------------------- args


def _args(**overrides):
    import argparse

    base = dict(
        dry_run=False,
        swarm="some-swarm",
        bots=3,
        hold_seconds=3.0,
        ttl=30,
        min_interval=0.25,
        max_interval=20.0,
        max_attempts_per_bot=10,
        max_attempts_total=300,
        i_have_checked_the_ru_budget=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_validate_args_accepts_a_sane_plan():
    bot_agent._validate_args(_args())  # should not raise


def test_validate_args_rejects_zero_bots():
    with pytest.raises(bot_agent.UsageError):
        bot_agent._validate_args(_args(bots=0))


def test_validate_args_rejects_hold_seconds_too_close_to_ttl():
    with pytest.raises(bot_agent.UsageError):
        bot_agent._validate_args(_args(hold_seconds=20.0, ttl=30))


def test_validate_args_rejects_plan_over_max_attempts_total():
    with pytest.raises(bot_agent.UsageError):
        bot_agent._validate_args(_args(bots=100, max_attempts_per_bot=100, max_attempts_total=300))


def test_validate_args_override_permits_over_budget_plan():
    bot_agent._validate_args(
        _args(
            bots=100,
            max_attempts_per_bot=100,
            max_attempts_total=300,
            i_have_checked_the_ru_budget=True,
        )
    )


def test_validate_args_refuses_dry_run_against_protected_swarm():
    with pytest.raises(bot_agent.UsageError):
        bot_agent._validate_args(_args(dry_run=True, swarm=bot_agent.PROTECTED_SWARM_ID))


def test_validate_args_override_permits_dry_run_against_protected_swarm():
    bot_agent._validate_args(
        _args(dry_run=True, swarm=bot_agent.PROTECTED_SWARM_ID, i_have_checked_the_ru_budget=True)
    )


def test_default_dry_run_swarm_is_not_the_protected_one():
    assert bot_agent._default_dry_run_swarm() != bot_agent.PROTECTED_SWARM_ID
    assert bot_agent._default_dry_run_swarm().startswith("bot-dryrun-")


# --------------------------------------------------------------------------- sleep


def test_interruptible_sleep_returns_promptly_once_stop_is_set():
    stop_event = threading.Event()
    stop_event.set()
    started = time.perf_counter()
    bot_agent._interruptible_sleep(5.0, stop_event, chunk=0.05)
    assert time.perf_counter() - started < 0.5


# --------------------------------------------------------------------------- fakes


class _FakeStore:
    """A correct, in-process stand-in: one holder per resource at a time."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.holders: dict[str, str] = {}
        self.registered: dict[str, dict] = {}
        self._next_id = 0

    def register_agent(self, framework, host, capabilities, agent_id):
        with self.lock:
            self.registered[agent_id] = {
                "framework": framework,
                "host": host,
                "capabilities": capabilities,
            }
        return agent_id

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
    """Deliberately violates single-winner: every attempt is granted."""

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


class _FakeClient:
    def __init__(self, store) -> None:
        self._store = store
        self.conn = object()

    def register_agent(self, framework, host, capabilities, agent_id):
        return self._store.register_agent(framework, host, capabilities, agent_id)

    def claim(self, resource, agent_id, intent, ttl):
        return self._store.claim(resource, agent_id, intent, ttl)

    def release(self, claim_id):
        return self._store.release(claim_id)

    def close(self) -> None:
        pass


class _RaisingHoldClient(_FakeClient):
    """A client whose grant path is fine, but the caller's hold step will raise --
    used to prove release() still happens (see test below)."""


# ---------------------------------------------------------------------- bot_loop


def _run_one_bot(
    store,
    identity,
    tasks,
    max_attempts=5,
    hold_seconds=0.01,
    ttl=30,
    client_cls=_FakeClient,
):
    report = bot_agent.BotReport(identity=identity)
    rate = bot_agent.AimdRate(interval=0.01, min_interval=0.005, max_interval=1.0)
    stop_event = threading.Event()
    deadline = time.monotonic() + 10
    bot_agent.bot_loop(
        client_factory=lambda: client_cls(store),
        identity=identity,
        tasks=tasks,
        rate=rate,
        ttl_seconds=ttl,
        hold_seconds=hold_seconds,
        max_attempts=max_attempts,
        deadline=deadline,
        stop_event=stop_event,
        report=report,
    )
    return report


def test_bot_loop_registers_before_claiming():
    store = _FakeStore()
    identity = bot_agent.BotIdentity(letter="A", host_label="test-host")
    _run_one_bot(store, identity, tasks=("t1",), max_attempts=1)
    assert identity.agent_id in store.registered
    assert store.registered[identity.agent_id]["framework"] == "bot"


def test_bot_loop_releases_every_granted_claim():
    store = _FakeStore()
    identity = bot_agent.BotIdentity(letter="A", host_label="test-host")
    report = _run_one_bot(store, identity, tasks=("t1", "t2", "t3"), max_attempts=6)
    assert len(report.granted) > 0
    assert store.holders == {}  # nothing left held
    for a in report.granted:
        assert a.held_until is not None


def test_bot_loop_releases_even_when_the_hold_step_raises(monkeypatch):
    """The core release-pflicht guarantee: an exception during the simulated hold
    must not prevent the claim from being released."""
    store = _FakeStore()
    identity = bot_agent.BotIdentity(letter="A", host_label="test-host")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated crash during hold")

    monkeypatch.setattr(bot_agent, "_interruptible_sleep", _boom)

    report = _run_one_bot(store, identity, tasks=("t1",), max_attempts=1)

    # The claim must have been released despite the injected crash.
    assert store.holders == {}
    granted = [a for a in report.attempts if a.claim_id is not None]
    assert len(granted) == 1
    assert granted[0].held_until is not None
    # And the loop must have survived to record the attempt as an error, not crash.
    assert any(a.outcome == "error" for a in report.attempts)


# ------------------------------------------------------------------- run_tournament


def test_run_tournament_against_a_correct_store_has_no_violation_and_releases_everything():
    store = _FakeStore()
    identities = bot_agent.build_identities(4, "test-host")
    report = bot_agent.run_tournament(
        client_factory=lambda: _FakeClient(store),
        mode="dry-run",
        swarm_id="test-swarm",
        identities=identities,
        tasks=("t1", "t2"),
        make_rate=lambda: bot_agent.AimdRate(interval=0.01, min_interval=0.005, max_interval=0.5),
        ttl_seconds=30,
        hold_seconds=0.01,
        max_attempts_per_bot=8,
        time_limit_seconds=15,
    )
    assert report.total_attempts == 4 * 8
    assert report.two_simultaneous_holders_detected is False
    assert report.overlap_violations == []
    assert report.unreleased_claims == []
    assert store.holders == {}


def test_run_tournament_against_a_broken_store_is_flagged():
    store = _BrokenStore()
    identities = bot_agent.build_identities(3, "test-host")
    report = bot_agent.run_tournament(
        client_factory=lambda: _FakeClient(store),
        mode="dry-run",
        swarm_id="test-swarm",
        identities=identities,
        tasks=("t1",),
        make_rate=lambda: bot_agent.AimdRate(interval=0.01, min_interval=0.005, max_interval=0.5),
        ttl_seconds=30,
        hold_seconds=0.01,
        max_attempts_per_bot=3,
        time_limit_seconds=15,
    )
    assert report.two_simultaneous_holders_detected is True
    assert len(report.overlap_violations) > 0


def test_as_dict_contains_display_names_not_just_technical_ids():
    store = _FakeStore()
    identities = bot_agent.build_identities(2, "test-host")
    report = bot_agent.run_tournament(
        client_factory=lambda: _FakeClient(store),
        mode="dry-run",
        swarm_id="test-swarm",
        identities=identities,
        tasks=("t1",),
        make_rate=lambda: bot_agent.AimdRate(interval=0.01, min_interval=0.005, max_interval=0.5),
        ttl_seconds=30,
        hold_seconds=0.01,
        max_attempts_per_bot=2,
        time_limit_seconds=15,
    )
    payload = report.as_dict()
    assert payload["bots"] == ["BotAgent A", "BotAgent B"]
    assert all("display_name" in b for b in payload["per_bot"])
    assert len(payload["interval_log"]) == report.total_attempts
