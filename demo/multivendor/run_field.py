"""Run three vendor agent CLIs against one shared task list, repeatedly.

Each round starts the configured agents as separate OS processes, back to back, and
waits for all of them. There is no starting gun and no barrier: whether two of them
reach for the same task at the same moment is decided by the operating system, which
is the point. A staged race would prove nothing.

Every round is a *fresh session* for every agent -- a new process with no memory of
the last round. Anything an agent knows about the others it learned from Roshambo.

Nothing here needs to know what the agents will do. It creates the workspace, writes
the launcher they call, starts them, and records when each process began and ended.
The numbers come from the database afterwards (see collect_evidence.py), not from
anything observed in this file.

Paths are taken from the environment so that no machine-specific path is baked into
the repository:

    ROSHAMBO_DSN_FILE              file containing the cluster connection string
    ROSHAMBO_SSLROOTCERT_FILE      CA bundle for the cluster (optional)
    ROSHAMBO_FIELD_CLAUDE_BIN      default "claude"
    ROSHAMBO_FIELD_AGY_BIN         default "agy"
    ROSHAMBO_FIELD_CODEX_COMPANION path to the Codex companion script
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RSB_PY = HERE / "rsb.py"
TASKS_MD = HERE / "TASKS.md"
PROMPT_MD = HERE / "prompts" / "agent.md"

# Stripped from every agent's environment. A third-party agent runtime ships its
# context to its vendor, and these carry (or point at) the cluster credentials.
SECRET_ENV_PREFIXES = ("ROSHAMBO_",)


@dataclass(frozen=True)
class Vendor:
    agent_id: str
    vendor: str

    def command(self, prompt: str, workspace: Path, env: dict[str, str]) -> list[str]:
        raise NotImplementedError


@dataclass(frozen=True)
class ClaudeVendor(Vendor):
    def command(self, prompt: str, workspace: Path, env: dict[str, str]) -> list[str]:
        return [
            env.get("ROSHAMBO_FIELD_CLAUDE_BIN", "claude"),
            "-p",
            prompt,
            # Deliberately narrow: no web access, no spawning further agents.
            "--allowedTools",
            "Read",
            "Write",
            "Edit",
            "Bash",
            "--permission-mode",
            "bypassPermissions",
        ]


@dataclass(frozen=True)
class CodexVendor(Vendor):
    def command(self, prompt: str, workspace: Path, env: dict[str, str]) -> list[str]:
        companion = env.get("ROSHAMBO_FIELD_CODEX_COMPANION")
        if not companion:
            raise SystemExit(
                "ROSHAMBO_FIELD_CODEX_COMPANION is not set; point it at the Codex "
                "companion script or drop 'codex' from --agents"
            )
        # -C fixes the writable root at the workspace. Without it the root would be
        # the git repository enclosing the current directory, which is far too wide
        # for an unattended agent.
        return [
            "node",
            companion,
            "task",
            "--write",
            "--effort",
            "low",
            "-C",
            str(workspace),
            prompt,
        ]


@dataclass(frozen=True)
class AgyVendor(Vendor):
    model: str = "gemini-3.6-flash"
    effort: str = "low"

    def command(self, prompt: str, workspace: Path, env: dict[str, str]) -> list[str]:
        # --add-dir is what actually grants write scope; the permission flag only
        # suppresses prompting and does not widen the workspace.
        return [
            env.get("ROSHAMBO_FIELD_AGY_BIN", "agy"),
            "--dangerously-skip-permissions",
            "--add-dir",
            str(workspace),
            "--model",
            self.model,
            "--effort",
            self.effort,
            "-p",
            prompt,
        ]


VENDORS: dict[str, Vendor] = {
    "claude": ClaudeVendor(agent_id="claude-code", vendor="anthropic"),
    "codex": CodexVendor(agent_id="codex", vendor="openai"),
    "agy": AgyVendor(agent_id="agy", vendor="google"),
}


@dataclass
class Invocation:
    round_index: int
    key: str
    agent_id: str
    vendor: str
    started_at: str
    ended_at: str = ""
    exit_code: int | None = None
    log: str = ""
    timed_out: bool = False


@dataclass
class RunRecord:
    swarm_id: str
    ttl: int
    started_at: str
    rounds: int
    invocations: list[Invocation] = field(default_factory=list)


def write_launcher(workspace: Path, env: dict[str, str], swarm_id: str) -> Path:
    """Write the one command the agents are told to run.

    Generated into the workspace rather than committed, because it necessarily
    contains machine-specific paths. The agents never receive the connection string
    or its location in their own environment -- this launcher sets it for the
    wrapper process alone.
    """
    dsn_file = env.get("ROSHAMBO_DSN_FILE", "").strip()
    if not dsn_file:
        raise SystemExit("ROSHAMBO_DSN_FILE is not set")

    cert = env.get("ROSHAMBO_SSLROOTCERT_FILE", "").strip()
    cert_line = f'set "ROSHAMBO_SSLROOTCERT_FILE={cert}"\r\n' if cert else ""

    launcher = workspace / "rsb.cmd"
    launcher.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        f'set "ROSHAMBO_DSN_FILE={dsn_file}"\r\n'
        f"{cert_line}"
        f'set "ROSHAMBO_SWARM_ID={swarm_id}"\r\n'
        'set "ROSHAMBO_EMBEDDING_PROVIDER=placeholder"\r\n'
        f'"{sys.executable}" "{RSB_PY}" %*\r\n',
        encoding="ascii",
    )
    return launcher


def prepare_workspace(workspace: Path, env: dict[str, str], swarm_id: str) -> Path:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "fieldkit").mkdir(exist_ok=True)
    (workspace / "tests").mkdir(exist_ok=True)
    (workspace / "_logs").mkdir(exist_ok=True)

    shutil.copyfile(TASKS_MD, workspace / "TASKS.md")

    index = workspace / "INDEX.md"
    if not index.exists():
        index.write_text(
            "# roshambo-fieldkit — completed tasks\n\n"
            "Every agent appends one line here after finishing a task. Because they all\n"
            "write to this one file, they have to take turns: it is claimed as\n"
            "`fieldkit:index` before it is touched.\n\n",
            encoding="utf-8",
        )

    return write_launcher(workspace, env, swarm_id)


def render_prompt(vendor: Vendor, workspace: Path, launcher: Path, ttl: int) -> str:
    template = PROMPT_MD.read_text(encoding="utf-8")
    return (
        template.replace("{AGENT_ID}", vendor.agent_id)
        .replace("{WORKDIR}", str(workspace))
        .replace("{RSB}", str(launcher))
        .replace("{TTL}", str(ttl))
    )


def agent_env(env: dict[str, str]) -> dict[str, str]:
    """The environment an agent process gets: ours, minus anything Roshambo-ish."""
    return {
        k: v
        for k, v in env.items()
        if not any(k.startswith(prefix) for prefix in SECRET_ENV_PREFIXES)
    }


def run_round(
    round_index: int,
    keys: list[str],
    workspace: Path,
    launcher: Path,
    ttl: int,
    timeout: int,
    env: dict[str, str],
) -> list[Invocation]:
    child_env = agent_env(env)
    started: list[tuple[str, subprocess.Popen, Invocation, Path]] = []

    for key in keys:
        vendor = VENDORS[key]
        prompt = render_prompt(vendor, workspace, launcher, ttl)
        log_path = workspace / "_logs" / f"round{round_index:02d}-{key}.log"
        handle = log_path.open("w", encoding="utf-8", errors="replace")
        record = Invocation(
            round_index=round_index,
            key=key,
            agent_id=vendor.agent_id,
            vendor=vendor.vendor,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        process = subprocess.Popen(  # noqa: S603
            vendor.command(prompt, workspace, env),
            cwd=str(workspace),
            env=child_env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        started.append((key, process, record, log_path))
        print(f"  round {round_index}: started {key} (pid {process.pid})", flush=True)

    results = []
    for key, process, record, log_path in started:
        try:
            record.exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            record.timed_out = True
            record.exit_code = None
            print(f"  round {round_index}: {key} timed out, killed", flush=True)
        record.ended_at = datetime.now(timezone.utc).isoformat()
        record.log = str(log_path.name)
        results.append(record)
        print(
            f"  round {round_index}: {key} finished (exit {record.exit_code})",
            flush=True,
        )

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="run directory, outside the repo")
    parser.add_argument("--swarm", required=True, help="ROSHAMBO_SWARM_ID for this run")
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--ttl", type=int, default=120, help="lease seconds")
    parser.add_argument("--timeout", type=int, default=600, help="seconds per invocation")
    parser.add_argument(
        "--agents",
        default="claude,codex,agy",
        help="comma-separated subset of: " + ", ".join(VENDORS),
    )
    parser.add_argument("--pause", type=float, default=0.0, help="seconds to wait between rounds")
    args = parser.parse_args(argv)

    keys = [k.strip() for k in args.agents.split(",") if k.strip()]
    unknown = [k for k in keys if k not in VENDORS]
    if unknown:
        raise SystemExit(f"unknown agent(s): {', '.join(unknown)}")

    env = dict(os.environ)
    workspace = Path(args.workspace).resolve()

    repo_root = HERE.parent.parent
    if repo_root == workspace or repo_root in workspace.parents:
        raise SystemExit(
            f"refusing to run inside the repository ({workspace}). Unattended agents "
            "get a writable root here; put the workspace somewhere else."
        )

    launcher = prepare_workspace(workspace, env, args.swarm)

    record = RunRecord(
        swarm_id=args.swarm,
        ttl=args.ttl,
        started_at=datetime.now(timezone.utc).isoformat(),
        rounds=args.rounds,
    )

    print(f"workspace: {workspace}")
    print(f"swarm:     {args.swarm}")
    print(f"agents:    {', '.join(keys)}")
    print(f"rounds:    {args.rounds} (ttl {args.ttl}s, timeout {args.timeout}s)")

    for round_index in range(1, args.rounds + 1):
        record.invocations.extend(
            run_round(round_index, keys, workspace, launcher, args.ttl, args.timeout, env)
        )
        manifest = workspace / "_logs" / "run.json"
        manifest.write_text(
            json.dumps(
                {
                    "swarm_id": record.swarm_id,
                    "ttl": record.ttl,
                    "started_at": record.started_at,
                    "rounds": record.rounds,
                    "invocations": [inv.__dict__ for inv in record.invocations],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        if args.pause and round_index < args.rounds:
            time.sleep(args.pause)

    print(f"\ndone. per-invocation logs and run.json in {workspace / '_logs'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
