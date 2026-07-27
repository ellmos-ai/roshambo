"""The heartbeat and takeover paths of the agent-facing wrapper, against a real cluster.

`tests/test_demo_rsb_status.py` covers the same status lines offline, by handing
`status_line` a payload directly. This drives the whole wrapper instead -- argument
parsing, the CLI call, the `who-has` lookup after a failed release -- because the part
most likely to break is not the formatting but the plumbing: `--resource` has to be
consumed by the wrapper and never reach `roshambo.cli`, and the holder lookup has to
happen against a lease that really was taken away.

The sequence is exactly what a field agent now hits: hold a lease, renew it, lose it to
a takeover, then try to renew and release a claim id that no longer identifies anything.
"""

from __future__ import annotations

import contextlib
import io
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demo" / "multivendor"))

import rsb  # noqa: E402

pytestmark = pytest.mark.live

# Short enough that a lease can be allowed to lapse inside a test. `heartbeat` renews
# using the *configured* TTL rather than the one the claim was made with, so this has to
# be set as well or the takeover below could never happen.
LEASE_SECONDS = "2"


@pytest.fixture
def run(live_dsn: str, schema_ready: None, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROSHAMBO_DSN", live_dsn)
    monkeypatch.setenv("ROSHAMBO_SWARM_ID", f"rsb-heartbeat-{uuid.uuid4().hex[:8]}")
    monkeypatch.setenv("ROSHAMBO_EMBEDDING_PROVIDER", "placeholder")
    monkeypatch.setenv("ROSHAMBO_LEASE_TTL_SECONDS", LEASE_SECONDS)

    def invoke(*args: str) -> tuple[str, int]:
        """Return the wrapper's first line and its exit code -- what an agent reads."""
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            code = rsb.main(list(args))
        first = captured.getvalue().strip().splitlines()
        return (first[0] if first else ""), code

    return invoke


def _claim_id(line: str) -> str:
    return next(part.split("=", 1)[1] for part in line.split() if part.startswith("claim_id="))


def test_heartbeat_renews_a_live_lease(run):
    resource = "verify:heartbeat:alive"
    granted, _ = run(
        "claim", resource, "--agent-id", "agent-a", "--intent", "working", "--ttl", "60"
    )

    line, code = run("heartbeat", _claim_id(granted))

    assert line == "ROSHAMBO RESULT=OK"
    assert code == 0


def test_heartbeat_after_a_takeover_names_the_new_holder(run):
    """A refused heartbeat must never read like "carry on"."""
    resource = "verify:heartbeat:taken"
    granted, _ = run(
        "claim", resource, "--agent-id", "agent-a", "--intent", "too slow", "--ttl", "1"
    )
    stale = _claim_id(granted)

    time.sleep(int(LEASE_SECONDS) + 1)
    run("claim", resource, "--agent-id", "agent-b", "--intent", "took it over", "--ttl", "60")

    line, code = run("--resource", resource, "heartbeat", stale)

    assert line.startswith("ROSHAMBO RESULT=EXPIRED")
    assert "held_by=agent-b" in line
    assert "intent=took it over" in line
    assert code == 3


def test_release_after_a_takeover_says_expired_not_noop(run):
    """The distinction the field run was missing: taken away, not nothing-to-do."""
    resource = "verify:release:taken"
    granted, _ = run(
        "claim", resource, "--agent-id", "agent-a", "--intent", "too slow", "--ttl", "1"
    )
    stale = _claim_id(granted)

    time.sleep(int(LEASE_SECONDS) + 1)
    run("claim", resource, "--agent-id", "agent-b", "--intent", "took it over", "--ttl", "60")

    line, code = run("--resource", resource, "release", stale)

    assert line.startswith("ROSHAMBO RESULT=EXPIRED")
    assert "held_by=agent-b" in line
    assert code == 3


def test_release_on_a_free_resource_is_still_a_plain_noop(run):
    """EXPIRED must mean "somebody else has it", not "the release failed"."""
    resource = "verify:release:free"

    line, code = run("--resource", resource, "release", str(uuid.uuid4()))

    assert line == "ROSHAMBO RESULT=NOOP"
    assert code == 3


def test_resource_hint_never_reaches_the_cli_parser(run):
    """`roshambo.cli` has no --resource; if it leaked through, argparse would exit 2.

    The prompts put the flag *after* the positional argument, so that is the form
    checked here.
    """
    resource = "verify:hint:passthrough"
    run("claim", resource, "--agent-id", "agent-a", "--intent", "holding", "--ttl", "60")

    line, code = run("who-has", resource, "--resource", resource)

    assert line.startswith("ROSHAMBO RESULT=OK")
    assert "held_by=agent-a" in line
    assert code == 0
