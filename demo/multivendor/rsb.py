"""A shell-shaped front door to Roshambo, for agents that are not ours.

Why this exists at all
----------------------
Roshambo's claim is that it coordinates agents *that do not know each other* --
different vendors, different machines, different sessions. To demonstrate that
with real vendor CLIs (Claude Code, Codex, Antigravity/agy), we need one calling
convention that all of them already have. That convention is "run a command and
read its exit code". It is the only interface every coding agent on earth
supports without being reconfigured.

The alternative -- teaching each vendor to speak our MCP server -- would mean
editing three different vendor configuration files. That is precisely the
per-vendor special case the product claims to make unnecessary, so proving the
claim that way would undercut it. MCP remains the native path for clients that
already speak it (see docs/EVIDENCE-iface.md); it is not the path under test here.

What this adds on top of `roshambo`
-----------------------------------
Exactly one thing: the connection string never reaches the agent. A third-party
agent runtime ships its context to its vendor's servers. Passing it a database
DSN would be a credential disclosure, so this wrapper reads the DSN itself, from
a file whose *path* is handed in via ``ROSHAMBO_DSN_FILE``, and puts it in the
environment of this process only. The agent sees the verbs and the exit codes.

What this adds, second thing
----------------------------
A status line on stdout. The exit code alone is not enough, and that is a
measurement, not a preference: asked to run a script that exits 3 and report the
code, the Antigravity agent reported 1. Each vendor drives a different shell
(bash, pwsh, its own), and a command that is not found also exits non-zero, so
"claim refused" and "your shell could not find the wrapper" are indistinguishable
from the exit code alone.

So every invocation prints, as its **first line**::

    ROSHAMBO RESULT=REGISTERED|GRANTED|DENIED|OK|NOOP|EXPIRED|ERROR ...

followed by the machine-readable payload. Exit codes are still set as
``roshambo.cli`` sets them (0 ok, 3 refused, 1 error) and remain usable by any
caller whose shell reports them faithfully -- they are simply no longer the only
channel. A front door meant for agents that were never built for it has to state
its answer in the one medium every one of them reads back reliably.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from pathlib import Path

DSN_FILE_ENV = "ROSHAMBO_DSN_FILE"
SSLROOTCERT_FILE_ENV = "ROSHAMBO_SSLROOTCERT_FILE"
DSN_SCHEMES = ("postgresql://", "postgres://")


def read_dsn_from_file(path: Path) -> str:
    """Return the first connection string in `path`.

    The file is allowed to be a human-written notes file with the DSN somewhere
    inside it, which is how operators actually keep these. Nothing read here is
    ever echoed: a parse failure reports the *path*, never the line, because the
    line is the secret.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SystemExit(f"rsb: cannot read {DSN_FILE_ENV} at {path}: {exc.strerror}") from None

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(DSN_SCHEMES):
            return line

    raise SystemExit(
        f"rsb: no connection string found in {path} "
        f"(expected a line starting with one of: {', '.join(DSN_SCHEMES)})"
    )


def with_root_cert(dsn: str, cert_path: str | None) -> str:
    """Attach `sslrootcert` unless the DSN already carries one.

    On Windows the bundled OpenSSL has no system trust store, so `verify-full`
    fails unless the CA bundle is named explicitly. Recorded the hard way in
    docs/EVIDENCE-cloud.md.
    """
    if not cert_path or "sslrootcert=" in dsn:
        return dsn
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}sslrootcert={cert_path}"


def resolve_dsn(env: dict[str, str]) -> str:
    dsn = (env.get("ROSHAMBO_DSN") or "").strip()
    if dsn:
        return dsn

    dsn_file = (env.get(DSN_FILE_ENV) or "").strip()
    if not dsn_file:
        raise SystemExit(
            f"rsb: neither ROSHAMBO_DSN nor {DSN_FILE_ENV} is set. "
            f"Point {DSN_FILE_ENV} at a file containing the cluster's connection string."
        )

    return with_root_cert(
        read_dsn_from_file(Path(dsn_file)),
        (env.get(SSLROOTCERT_FILE_ENV) or "").strip() or None,
    )


def _verb(args: list[str]) -> str:
    """The subcommand, ignoring any leading global flags."""
    return next((a for a in args if not a.startswith("-")), "")


RESOURCE_HINT_FLAG = "--resource"


def take_resource_hint(args: list[str]) -> tuple[list[str], str | None]:
    """Pull `--resource X` out of the argument list; it is ours, not the CLI's.

    `release` takes a claim_id and nothing else, and after a takeover that id exists
    nowhere: `ACQUIRE_SQL` regenerates `claim_id` on takeover, so the row that would
    name the new holder can no longer be found from the old id. The resource is the
    only thing that survives a takeover, so it has to come from the caller.
    """
    kept: list[str] = []
    hint: str | None = None
    i = 0
    while i < len(args):
        if args[i] == RESOURCE_HINT_FLAG and i + 1 < len(args):
            hint = args[i + 1]
            i += 2
            continue
        kept.append(args[i])
        i += 1
    return kept, hint


def current_holder(resource: str) -> dict | None:
    """Who holds `resource` right now, or None if it is free or unreadable.

    Only ever called *after* a release has already failed, to explain why. It reports
    and never decides, so it cannot reintroduce a check-then-act race.
    """
    from roshambo.cli import main as cli_main

    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            cli_main(["--json", "who-has", resource])
        payload = json.loads(captured.getvalue())
    except Exception:  # noqa: BLE001 - a failed diagnosis must not mask the release result
        return None
    return payload if isinstance(payload, dict) and payload.get("held") else None


def status_line(verb: str, exit_code: int, payload: object, holder: dict | None = None) -> str:
    """One unambiguous line an agent can match on without parsing anything else."""
    if exit_code == 1:
        return "ROSHAMBO RESULT=ERROR"

    if verb == "register-agent" and isinstance(payload, dict):
        return (
            f"ROSHAMBO RESULT=REGISTERED agent_id={payload.get('agent_id')} "
            f"framework={payload.get('framework')} host={payload.get('host')}"
        )

    if verb == "claim" and isinstance(payload, dict):
        if payload.get("granted"):
            return (
                f"ROSHAMBO RESULT=GRANTED resource={payload.get('resource')} "
                f"claim_id={payload.get('claim_id')} expires_at={payload.get('expires_at')}"
            )
        return (
            f"ROSHAMBO RESULT=DENIED resource={payload.get('resource')} "
            f"held_by={payload.get('held_by')} expires_at={payload.get('expires_at')} "
            f"intent={payload.get('intent')}"
        )

    if verb == "heartbeat" and isinstance(payload, dict):
        if payload.get("alive"):
            return "ROSHAMBO RESULT=OK"
        # A refused heartbeat is never "carry on". The lease is gone and deliberately
        # not renewable (see leases.py: a lapsed lease may already belong to someone
        # else). Saying plain OK here would repeat the NOOP mistake in a new place.
        if holder:
            return (
                f"ROSHAMBO RESULT=EXPIRED held_by={holder.get('agent_id')} "
                f"expires_at={holder.get('expires_at')} intent={holder.get('intent')}"
            )
        return "ROSHAMBO RESULT=EXPIRED held_by=unknown"

    if verb == "release" and isinstance(payload, dict):
        if payload.get("released"):
            return "ROSHAMBO RESULT=OK"
        # A failed release has two very different causes, and "NOOP" read like the
        # harmless one. In the field run two agents were told NOOP after their lease
        # had lapsed and been re-granted; both read it as "nothing to do" and committed
        # work nobody was waiting for any more. Name the takeover when there is one.
        if holder:
            return (
                f"ROSHAMBO RESULT=EXPIRED held_by={holder.get('agent_id')} "
                f"expires_at={holder.get('expires_at')} intent={holder.get('intent')}"
            )
        # Nobody holds it: already released, or never held. That really is a no-op.
        return "ROSHAMBO RESULT=NOOP"

    if verb == "who-has" and isinstance(payload, dict):
        if not payload.get("held"):
            return "ROSHAMBO RESULT=OK free=true"
        return (
            f"ROSHAMBO RESULT=OK free=false held_by={payload.get('agent_id')} "
            f"expires_at={payload.get('expires_at')}"
        )

    return "ROSHAMBO RESULT=OK"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    args, resource_hint = take_resource_hint(args)
    os.environ["ROSHAMBO_DSN"] = resolve_dsn(dict(os.environ))

    from roshambo.cli import main as cli_main

    # The payload is taken in machine-readable form so the status line can be built
    # from the same values the caller sees, rather than by re-parsing prose.
    if "--json" not in args:
        args.insert(0, "--json")

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        exit_code = cli_main(args)
    raw = captured.getvalue()

    try:
        payload: object = json.loads(raw)
    except json.JSONDecodeError:
        payload = None

    verb = _verb(args)
    holder = None
    if resource_hint and isinstance(payload, dict):
        lost_the_lease = (verb == "release" and not payload.get("released")) or (
            verb == "heartbeat" and not payload.get("alive")
        )
        if lost_the_lease:
            holder = current_holder(resource_hint)

    print(status_line(verb, exit_code, payload, holder))
    if raw.strip():
        print(raw, end="" if raw.endswith("\n") else "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
