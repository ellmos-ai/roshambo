#!/usr/bin/env python3
"""Wrap the ``ccloud`` CLI (CockroachDB Cloud) for scripted/agent-driven provisioning.

Every subcommand prints exactly one JSON object to stdout -- ``{"ok": true, ...}`` on
success, ``{"ok": false, "error": "..."}`` on failure -- and sets the process exit code
accordingly. That is deliberate: the whole point of wrapping the CLI instead of telling a
human to run it by hand is that a script (or an agent) can call this file and always get
one parseable object back, never a bare stack trace or a truncated table. ``ccloud``
itself already has a ``-o json`` flag for exactly this purpose; subcommands that use it
pass ccloud's own JSON straight through inside the ``"result"`` key.

Subcommands
-----------

``check``                  Confirm the ``ccloud`` binary is installed and runnable.
``create-cluster``          ``ccloud cluster create serverless <name> <region> --cloud AWS -o json``
``list-clusters``           ``ccloud cluster list -o json``
``connection-string``       ``ccloud cluster connection-string <name> --database <db>
                             --sql-user <user> -o json``
``create-service-account``  Best-effort, **unverified syntax** -- see the function docstring.
``create-backup``           Best-effort, **unverified syntax** -- see the function docstring.

What is and isn't verified
---------------------------

Researched 2026-07-25 against the CockroachDB Cloud "AI agents" blog post and the
official "Get Started with the ccloud CLI" documentation (URLs in docs/EVIDENCE-aws.md).
Confirmed from those sources: install path (``%appdata%\\ccloud`` on Windows), the
``ccloud auth login`` command, the noun-verb command pattern, the global ``-o json``
flag, and the three concrete command forms used by ``create-cluster``,
``list-clusters``, and ``connection-string`` above.

**Not verified**: the exact flags for ``service-account create`` and any ``backup``
subcommand -- the reference page listing every ``ccloud <noun> --help`` was not
reachable during research, and no ``ccloud`` binary is installed in this environment to
check directly (verified: ``which ccloud`` -> not found, see docs/EVIDENCE-aws.md).
Those two subcommands below follow the same noun-verb convention as a best guess, print
the exact command they are about to run before running it, and surface ccloud's own
error message on failure rather than swallowing it -- so a real ``ccloud`` install
either confirms the guess or fails legibly enough to correct it.

Nothing in this file has been run against a real CockroachDB Cloud account. It is
written to fail cleanly and legibly (missing binary, missing auth, wrong flags) rather
than to silently no-op or invent output when it cannot actually run ``ccloud``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from typing import Any


class ProvisionError(RuntimeError):
    """Raised for problems this script can explain better than ccloud's raw output."""


def _ccloud_path() -> str:
    path = shutil.which("ccloud")
    if path is None:
        raise ProvisionError(
            "the 'ccloud' CLI is not on PATH. Install: "
            "https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-get-started "
            "(Windows: downloads to %appdata%\\ccloud, add that to PATH), then "
            "'ccloud auth login'."
        )
    return path


def _run_ccloud(args: list[str], *, json_output: bool = True, timeout: int = 120) -> Any:
    """Run ``ccloud <args>``, optionally appending ``-o json``. Returns parsed JSON
    (or raw stdout text if ``json_output`` is False). Raises ProvisionError with
    ccloud's own stderr on a non-zero exit -- never swallows the CLI's own message.
    """
    binary = _ccloud_path()
    full_args = [binary, *args]
    if json_output:
        full_args += ["-o", "json"]
    print(f"$ {' '.join(full_args)}", file=sys.stderr)
    try:
        result = subprocess.run(full_args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ProvisionError(
            f"ccloud command timed out after {timeout}s: {' '.join(full_args)}"
        ) from exc

    if result.returncode != 0:
        raise ProvisionError(
            f"ccloud exited {result.returncode}: {' '.join(full_args)}\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
    if not json_output:
        return result.stdout.strip()
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProvisionError(
            f"ccloud -o json did not return valid JSON: {result.stdout[:500]!r}"
        ) from exc


# ------------------------------------------------------------------------------ check


def cmd_check(_args: argparse.Namespace) -> dict:
    path = shutil.which("ccloud")
    if path is None:
        return {"ccloud_found": False, "path": None}
    # `--help` is the one flag essentially every CLI supports; used here purely to
    # confirm the binary is runnable, not to assert a specific "version" subcommand
    # exists (that command form is not confirmed -- see module docstring).
    result = subprocess.run([path, "--help"], capture_output=True, text=True, timeout=15)
    return {
        "ccloud_found": True,
        "path": path,
        "help_exit_code": result.returncode,
        "runnable": result.returncode == 0,
    }


# ------------------------------------------------------------------- create-cluster


def cmd_create_cluster(args: argparse.Namespace) -> dict:
    result = _run_ccloud(
        ["cluster", "create", "serverless", args.name, args.region, "--cloud", args.cloud]
    )
    return {"result": result}


# -------------------------------------------------------------------- list-clusters


def cmd_list_clusters(_args: argparse.Namespace) -> dict:
    result = _run_ccloud(["cluster", "list"])
    return {"result": result}


# ---------------------------------------------------------------- connection-string


def cmd_connection_string(args: argparse.Namespace) -> dict:
    result = _run_ccloud(
        [
            "cluster",
            "connection-string",
            args.name,
            "--database",
            args.database,
            "--sql-user",
            args.sql_user,
        ]
    )
    return {"result": result}


# ------------------------------------------------------------- create-service-account


def cmd_create_service_account(args: argparse.Namespace) -> dict:
    """**Unverified command syntax** -- see module docstring. Best guess, following the
    documented noun-verb pattern (``ccloud <noun> <verb>``) with ``service-account`` as
    the noun. If your ``ccloud`` version rejects this, run ``ccloud service-account
    --help`` yourself and adjust; this function's only job is to fail with ccloud's own
    error message rather than pretend to have succeeded.
    """
    result = _run_ccloud(["service-account", "create", args.name, "--role", args.role])
    return {"result": result, "verified": False}


# ------------------------------------------------------------------- create-backup


def cmd_create_backup(args: argparse.Namespace) -> dict:
    """**Unverified command syntax** -- see module docstring and ``create-service-account``."""
    result = _run_ccloud(["cluster", "backup", "create", args.cluster_name])
    return {"result": result, "verified": False}


# ----------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Wrap the ccloud CLI for scripted CockroachDB Cloud provisioning. "
        "Every subcommand prints one JSON object to stdout."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="confirm ccloud is installed and runnable")
    p_check.set_defaults(func=cmd_check)

    p_create = sub.add_parser("create-cluster", help="ccloud cluster create serverless ...")
    p_create.add_argument("name")
    p_create.add_argument("region", help="e.g. us-east-1")
    p_create.add_argument("--cloud", default="AWS", choices=["AWS", "GCP", "AZURE"])
    p_create.set_defaults(func=cmd_create_cluster)

    p_list = sub.add_parser("list-clusters", help="ccloud cluster list")
    p_list.set_defaults(func=cmd_list_clusters)

    p_conn = sub.add_parser("connection-string", help="ccloud cluster connection-string ...")
    p_conn.add_argument("name")
    p_conn.add_argument("--database", default="roshambo")
    p_conn.add_argument("--sql-user", default="root")
    p_conn.set_defaults(func=cmd_connection_string)

    p_sa = sub.add_parser(
        "create-service-account", help="UNVERIFIED syntax -- see module docstring"
    )
    p_sa.add_argument("name")
    p_sa.add_argument("--role", default="admin")
    p_sa.set_defaults(func=cmd_create_service_account)

    p_backup = sub.add_parser("create-backup", help="UNVERIFIED syntax -- see module docstring")
    p_backup.add_argument("cluster_name")
    p_backup.set_defaults(func=cmd_create_backup)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.func(args)
        print(json.dumps({"ok": True, "command": args.command, **payload}, indent=2))
        return 0
    except ProvisionError as exc:
        print(json.dumps({"ok": False, "command": args.command, "error": str(exc)}, indent=2))
        return 1
    except Exception as exc:  # never let this exit with a raw traceback -- always JSON
        print(
            json.dumps(
                {
                    "ok": False,
                    "command": args.command,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
