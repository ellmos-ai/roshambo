"""Lease semantics against a real CockroachDB cluster."""

from __future__ import annotations

import time

import pytest

from roshambo.memory import Roshambo
from roshambo.models import Claim, ClaimDenied

pytestmark = pytest.mark.live


def test_first_claim_is_granted(roshambo: Roshambo):
    result = roshambo.claim("repo:demo:src/parser.py", "agent-a", "rewrite the tokenizer")
    assert isinstance(result, Claim)
    assert result.resource == "repo:demo:src/parser.py"
    assert result.agent_id == "agent-a"
    assert result.claim_id


def test_second_claim_is_denied_and_says_who_holds_it(roshambo: Roshambo):
    """A denial is a product feature: it has to name the holder and their intent.

    Telling an agent only "no" leaves it to poll or to duplicate the work; telling it
    who is working on what lets it choose something else.
    """
    roshambo.claim("repo:demo:src/parser.py", "agent-a", "rewrite the tokenizer")
    denied = roshambo.claim("repo:demo:src/parser.py", "agent-b", "add error recovery")

    assert isinstance(denied, ClaimDenied)
    assert denied.held_by == "agent-a"
    assert denied.intent == "rewrite the tokenizer"
    assert denied.expires_at is not None


def test_different_resources_do_not_collide(roshambo: Roshambo):
    a = roshambo.claim("repo:demo:a.py", "agent-a", "work")
    b = roshambo.claim("repo:demo:b.py", "agent-b", "work")
    assert isinstance(a, Claim)
    assert isinstance(b, Claim)


def test_who_has_reports_the_holder_and_the_intent(roshambo: Roshambo):
    """The intent is the half that makes the answer actionable.

    Knowing that `agent-a` holds the file lets a second agent wait; knowing that it is
    "rewriting the tokenizer" lets it pick work that will not conflict, or recognise that
    its own task is already being done.
    """
    assert roshambo.who_has("repo:demo:c.py") is None
    granted = roshambo.claim("repo:demo:c.py", "agent-a", "rewrite the tokenizer")
    assert isinstance(granted, Claim)

    held = roshambo.who_has("repo:demo:c.py")
    assert held is not None
    assert held.agent_id == "agent-a"
    assert held.claim_id == granted.claim_id
    assert held.intent == "rewrite the tokenizer"
    assert held.resource == "repo:demo:c.py"
    assert held.expires_at == granted.expires_at


def test_a_foreign_agent_cannot_release_a_lease_it_does_not_hold(roshambo: Roshambo):
    """Documents the authority model: the `claim_id` *is* the authority.

    `release()` takes a claim_id and nothing else, so what protects a lease is that a
    foreign agent does not have that id — not a check on the caller's name. This test
    pins the consequence: guessing wrong changes nothing and the holder keeps the lease.
    An agent that legitimately learns another's claim_id can release it, which is why
    `who_has()` deliberately reports the holder and the intent but never the claim_id.
    """
    granted = roshambo.claim("repo:demo:k.py", "agent-a", "rewrite the tokenizer")
    assert isinstance(granted, Claim)

    for guess in (
        "00000000-0000-0000-0000-000000000000",
        "11111111-2222-3333-4444-555555555555",
    ):
        assert roshambo.release(guess) is False

    still_held = roshambo.who_has("repo:demo:k.py")
    assert still_held is not None
    assert still_held.claim_id == granted.claim_id
    assert still_held.agent_id == "agent-a"

    denied = roshambo.claim("repo:demo:k.py", "agent-b", "sneak in after a failed release")
    assert isinstance(denied, ClaimDenied)
    assert denied.held_by == "agent-a"


def test_who_has_does_not_leak_the_claim_id(roshambo: Roshambo):
    """A `ClaimDenied` must not hand the loser the capability to release the winner."""
    roshambo.claim("repo:demo:l.py", "agent-a", "work")
    denied = roshambo.claim("repo:demo:l.py", "agent-b", "other work")

    assert isinstance(denied, ClaimDenied)
    assert not hasattr(denied, "claim_id")


def test_release_frees_the_resource(roshambo: Roshambo):
    granted = roshambo.claim("repo:demo:d.py", "agent-a", "work")
    assert isinstance(granted, Claim)

    assert roshambo.release(granted.claim_id) is True
    assert roshambo.who_has("repo:demo:d.py") is None

    after = roshambo.claim("repo:demo:d.py", "agent-b", "other work")
    assert isinstance(after, Claim)


def test_release_of_an_unknown_claim_is_false_not_an_error(roshambo: Roshambo):
    assert roshambo.release("00000000-0000-0000-0000-000000000000") is False


def test_expired_lease_is_taken_over(roshambo: Roshambo):
    """A crashed agent must not be able to block a resource forever."""
    granted = roshambo.claim("repo:demo:e.py", "agent-crashed", "work it never finished", 1)
    assert isinstance(granted, Claim)

    denied = roshambo.claim("repo:demo:e.py", "agent-b", "same work")
    assert isinstance(denied, ClaimDenied)

    time.sleep(1.5)

    taken_over = roshambo.claim("repo:demo:e.py", "agent-b", "same work")
    assert isinstance(taken_over, Claim)
    assert taken_over.agent_id == "agent-b"
    assert taken_over.claim_id != granted.claim_id


def test_heartbeat_extends_a_live_lease(roshambo: Roshambo):
    granted = roshambo.claim("repo:demo:f.py", "agent-a", "long job", 30)
    assert isinstance(granted, Claim)

    assert roshambo.heartbeat(granted.claim_id) is True
    held = roshambo.who_has("repo:demo:f.py")
    assert held is not None
    assert held.expires_at > granted.expires_at


def test_heartbeat_cannot_resurrect_an_expired_lease(roshambo: Roshambo):
    """Once a lease lapses it may already belong to somebody else.

    Letting a slow agent renew it after the fact would silently produce two holders,
    which is the exact failure the lease exists to prevent.
    """
    granted = roshambo.claim("repo:demo:g.py", "agent-slow", "work", 1)
    assert isinstance(granted, Claim)
    time.sleep(1.5)

    assert roshambo.heartbeat(granted.claim_id) is False


def test_heartbeat_of_a_taken_over_lease_is_false(roshambo: Roshambo):
    first = roshambo.claim("repo:demo:h.py", "agent-slow", "work", 1)
    assert isinstance(first, Claim)
    time.sleep(1.5)

    second = roshambo.claim("repo:demo:h.py", "agent-fast", "same work")
    assert isinstance(second, Claim)

    assert roshambo.heartbeat(first.claim_id) is False
    assert roshambo.heartbeat(second.claim_id) is True


def test_release_after_takeover_does_not_steal_the_new_lease(roshambo: Roshambo):
    """The old holder waking up late must not be able to release somebody else's lease."""
    first = roshambo.claim("repo:demo:i.py", "agent-slow", "work", 1)
    assert isinstance(first, Claim)
    time.sleep(1.5)

    second = roshambo.claim("repo:demo:i.py", "agent-fast", "same work")
    assert isinstance(second, Claim)

    assert roshambo.release(first.claim_id) is False
    held = roshambo.who_has("repo:demo:i.py")
    assert held is not None
    assert held.agent_id == "agent-fast"


def test_reclaiming_your_own_live_lease_is_denied_not_extended(roshambo: Roshambo):
    """Documenting the chosen semantics: `claim` is not idempotent per agent.

    The lease belongs to a claim_id, not to an agent name, so an agent that lost track
    of its claim_id gets a denial (naming itself) rather than a silent second lease.
    Renewal is `heartbeat`.
    """
    granted = roshambo.claim("repo:demo:j.py", "agent-a", "work")
    assert isinstance(granted, Claim)

    again = roshambo.claim("repo:demo:j.py", "agent-a", "work")
    assert isinstance(again, ClaimDenied)
    assert again.held_by == "agent-a"
