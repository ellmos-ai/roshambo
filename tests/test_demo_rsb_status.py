"""The status line the third-party agents actually read (demo/multivendor/rsb.py).

Vendor agents are told to match on the first line and ignore the exit code, so that
line is the whole interface for them -- it is worth testing on its own, without a
cluster.

The case that matters here was found in the field run `starmap-2026-07-27`: two agents
released a lease that had already lapsed and been re-granted to somebody else, were
answered `NOOP`, read that as "nothing to do", and committed work that nobody was
waiting for any more. `NOOP` was true and useless. These tests pin the distinction it
was missing:

* the lease lapsed and somebody else holds the resource now -> `EXPIRED`, naming them;
* the lease is simply gone and nobody holds it -> `NOOP`, which is honest.

Conflating the two would replace one misleading answer with another, so the free case
is asserted just as explicitly as the taken-over one.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from demo.multivendor.rsb import status_line, take_resource_hint  # noqa: E402


class TestResourceHint:
    """`--resource` belongs to the wrapper and must never reach `roshambo.cli`."""

    def test_hint_is_removed_from_the_arguments(self):
        args, hint = take_resource_hint(
            ["release", "abc-123", "--resource", "starmap:task:01"]
        )
        assert args == ["release", "abc-123"]
        assert hint == "starmap:task:01"

    def test_absent_hint_leaves_arguments_untouched(self):
        args, hint = take_resource_hint(["release", "abc-123"])
        assert args == ["release", "abc-123"]
        assert hint is None

    def test_trailing_flag_without_a_value_is_left_alone(self):
        # Better to hand the CLI an argument it will reject loudly than to silently
        # swallow a flag the caller clearly meant to use.
        args, hint = take_resource_hint(["release", "abc-123", "--resource"])
        assert args == ["release", "abc-123", "--resource"]
        assert hint is None


class TestReleaseStatusLine:
    def test_successful_release_is_ok(self):
        assert status_line("release", 0, {"released": True}) == "ROSHAMBO RESULT=OK"

    def test_failed_release_with_a_current_holder_reports_expired(self):
        line = status_line(
            "release",
            3,
            {"released": False},
            holder={
                "agent_id": "codex-2",
                "expires_at": "2026-07-27T02:14:00+02:00",
                "intent": "build task 01",
            },
        )
        assert line.startswith("ROSHAMBO RESULT=EXPIRED ")
        assert "held_by=codex-2" in line
        assert "expires_at=2026-07-27T02:14:00+02:00" in line
        assert "intent=build task 01" in line

    def test_failed_release_with_nobody_holding_it_stays_noop(self):
        # Already released, or never held. Calling this EXPIRED would be a new lie.
        assert status_line("release", 3, {"released": False}) == "ROSHAMBO RESULT=NOOP"

    def test_holder_is_ignored_when_the_release_succeeded(self):
        line = status_line("release", 0, {"released": True}, holder={"agent_id": "x"})
        assert line == "ROSHAMBO RESULT=OK"

    def test_a_hard_error_outranks_everything(self):
        assert status_line("release", 1, {"released": False}) == "ROSHAMBO RESULT=ERROR"


class TestHeartbeatStatusLine:
    """A refused heartbeat must never read as "carry on"."""

    def test_a_live_lease_is_ok(self):
        assert status_line("heartbeat", 0, {"alive": True}) == "ROSHAMBO RESULT=OK"

    def test_a_lapsed_lease_names_whoever_holds_it_now(self):
        line = status_line(
            "heartbeat",
            3,
            {"alive": False},
            holder={
                "agent_id": "agy-3",
                "expires_at": "2026-07-27T02:31:00+02:00",
                "intent": "build task 04",
            },
        )
        assert line.startswith("ROSHAMBO RESULT=EXPIRED ")
        assert "held_by=agy-3" in line

    def test_a_lapsed_lease_is_still_expired_without_a_resource_hint(self):
        # The lease is provably gone either way; only the holder is unknown. Reporting
        # OK here would be the NOOP mistake wearing a different verb.
        line = status_line("heartbeat", 3, {"alive": False})
        assert line == "ROSHAMBO RESULT=EXPIRED held_by=unknown"
