"""`roshambo.cli` argument parsing and top-level dispatch. No database required.

Before this file existed, `src/roshambo/cli.py` (294 lines, the whole command-line
surface) had no offline test coverage at all -- the only tests that touched it
(`test_demo_rsb_status.py`, `test_demo_rsb_heartbeat_live.py`, `test_host_identity.py`)
are `live`-marked and only run against a real CockroachDB cluster. That gap hid a real
bug: `roshambo status --json` (the flag typed *after* the subcommand, matching every
other subcommand flag) failed with `argparse: error: unrecognized arguments: --json`
and exit code 2, because `--json` was only ever defined on the top-level parser, and
`argparse` does not look for a top-level optional once a subparser has started
consuming arguments. Only `roshambo --json status` (flag *before* the subcommand)
worked. Fixed by giving every subparser its own `--json`, copied via a shared
`argparse.ArgumentParser(add_help=False)` parent -- with `default=argparse.SUPPRESS`
on that copy specifically, because the naive version of the fix (a plain `default=False`
copy) reintroduces the bug in the *other* direction: `_SubParsersAction.__call__`
unconditionally merges the subparser's namespace over the top-level one, so a bare
`False` default on the subparser silently clobbers a `--json` already parsed before the
subcommand.
"""

from __future__ import annotations

import pytest

from roshambo.cli import _build_parser, main

# One representative, minimally-valid argv per subcommand, split so a test can insert
# `--json` either right after `[0]` (before the subcommand) or at the end (after every
# other flag the subcommand needs).
_SUBCOMMAND_ARGV: dict[str, list[str]] = {
    "init-schema": ["init-schema"],
    "status": ["status"],
    "register-agent": [
        "register-agent",
        "--agent-id",
        "a1",
        "--framework",
        "claude-code",
        "--host",
        "h1",
    ],
    "claim": ["claim", "res1", "--agent-id", "a1", "--intent", "digging"],
    "heartbeat": ["heartbeat", "claim-1"],
    "release": ["release", "claim-1"],
    "who-has": ["who-has", "res1"],
    "remember": [
        "remember",
        "topic",
        "--approach",
        "tried x",
        "--outcome",
        "success",
        "--evidence",
        "it worked",
    ],
    "recall": ["recall", "query"],
    "decide": [
        "decide",
        "question",
        "--choice",
        "yes",
        "--rationale",
        "because",
        "--confidence",
        "high",
        "--provenance",
        "agent-inferred",
    ],
}


@pytest.mark.parametrize("command", sorted(_SUBCOMMAND_ARGV))
def test_json_flag_works_after_the_subcommand(command):
    """The form every subcommand's own flags already use, and the one that was broken."""
    argv = [*_SUBCOMMAND_ARGV[command], "--json"]
    args = _build_parser().parse_args(argv)
    assert args.json is True
    assert args.command == command


@pytest.mark.parametrize("command", sorted(_SUBCOMMAND_ARGV))
def test_json_flag_still_works_before_the_subcommand(command):
    """The form that worked even before this fix -- must not regress."""
    subcommand, *rest = _SUBCOMMAND_ARGV[command]
    args = _build_parser().parse_args(["--json", subcommand, *rest])
    assert args.json is True
    assert args.command == command


@pytest.mark.parametrize("command", sorted(_SUBCOMMAND_ARGV))
def test_json_flag_defaults_to_false(command):
    args = _build_parser().parse_args(_SUBCOMMAND_ARGV[command])
    assert args.json is False


def test_json_before_subcommand_is_not_clobbered_by_the_subparsers_default():
    """Regression guard for the fix itself, not just the original bug.

    `argparse`'s `_SubParsersAction` always merges the chosen subparser's namespace
    over the parent's, attribute by attribute. If the subparser's own `--json` copy
    used a plain `default=False` (the first fix attempted here), that merge would
    silently overwrite a `--json` already parsed before the subcommand -- turning
    `roshambo --json status` from `json=True` back into `json=False` without raising
    anything. The subparser copy must use `default=argparse.SUPPRESS` so it contributes
    no `json` key at all when `--json` was not given on its own side.
    """
    args = _build_parser().parse_args(["--json", "status"])
    assert args.json is True


def test_missing_required_argument_exits_with_usage_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        _build_parser().parse_args(["claim", "res1"])  # missing --agent-id/--intent
    assert excinfo.value.code == 2
    assert "--agent-id" in capsys.readouterr().err


def test_unknown_command_exits_with_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        _build_parser().parse_args(["not-a-real-command"])
    assert excinfo.value.code == 2


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        _build_parser().parse_args(["--help"])
    assert excinfo.value.code == 0
    assert "roshambo" in capsys.readouterr().out


def test_main_with_json_after_subcommand_reaches_config_loading(monkeypatch):
    """End-to-end through `main()`, not just the parser.

    With `--json` in the previously-broken position, `main()` never even reached
    `load_config()` -- `_build_parser().parse_args()` raised `SystemExit(2)` first. With
    `ROSHAMBO_DSN` unset, a `main()` that gets past argument parsing fails one step
    later, in `load_config()`, which is caught and turned into a clean `return 1` (see
    `roshambo.cli.main`'s `except RoshamboError` branch) rather than an argparse usage
    error. That return value -- not a `SystemExit` -- is the evidence that parsing
    itself succeeded.
    """
    monkeypatch.delenv("ROSHAMBO_DSN", raising=False)
    assert main(["status", "--json"]) == 1


def test_main_reports_config_error_on_stderr(monkeypatch, capsys):
    monkeypatch.delenv("ROSHAMBO_DSN", raising=False)
    exit_code = main(["status"])
    assert exit_code == 1
    assert "ROSHAMBO_DSN" in capsys.readouterr().err
