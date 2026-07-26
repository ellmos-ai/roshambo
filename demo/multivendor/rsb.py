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

Exit codes are inherited from ``roshambo.cli`` and are the actual protocol:
0 = did what you asked, 3 = refused (claim denied / unknown claim), 1 = error.
"""

from __future__ import annotations

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
        raise SystemExit(
            f"rsb: cannot read {DSN_FILE_ENV} at {path}: {exc.strerror}"
        ) from None

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


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    os.environ["ROSHAMBO_DSN"] = resolve_dsn(dict(os.environ))

    from roshambo.cli import main as cli_main

    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
