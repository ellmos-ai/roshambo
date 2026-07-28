"""roshambo-mcp: the agent-facing MCP server for Roshambo.

Roshambo has two deliberately separate MCP paths onto the same CockroachDB cluster:

- The **CockroachDB Managed MCP Server** (``https://cockroachlabs.cloud/mcp``) is the
  human-adjacent path: schema introspection, ad-hoc analysis, operational questions.
  Read-only by default. See ``docs/mcp-managed.md``.
- **This server** (``roshambo-mcp``) is the agent-adjacent path: a narrow, checked set of
  verbs -- ``register_agent``, ``claim``, ``heartbeat``, ``release``, ``remember``,
  ``recall``, ``decide``, ``status`` --
  that enforce Roshambo's invariants (lease semantics, provenance on every decision). It
  does **not** expose a tool that accepts free-form SQL. That is a deliberate security
  boundary, not an oversight: an agent that can write arbitrary SQL can violate the
  memory's invariants (for example, releasing a lease it does not hold, or writing a
  trail with no evidence). The managed server stays the inspection path; this one stays
  the write path.

Built on the official Model Context Protocol Python SDK's ``FastMCP`` (package ``mcp``,
see ``pyproject.toml`` -- ``mcp>=1.2``). Run directly with ``python -m roshambo.mcp.server``
or via the ``roshambo-mcp`` console script installed by this package; both default to the
stdio transport, which is what Claude Code, Cursor and other local MCP clients expect
from a locally-launched server.

The tools below are thin wrappers around ``roshambo.memory.Roshambo`` (owned by the Core
lane, see ``CONTRACT.md``). That module may not exist yet at import time -- the import
is deferred and defensive so this server still starts and lists its eight tools even
before ``roshambo.memory`` lands; only *calling* a tool then fails, with a clear message
naming the missing module.
"""

from __future__ import annotations

import dataclasses
import os
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "roshambo-mcp",
    instructions=(
        "Roshambo is an agentic memory fabric on CockroachDB: serializable leases so two "
        "agents never claim the same work, and a vector index so an agent can ask, "
        "before it starts, 'has anyone tried this -- and how did it end?'\n\n"
        "Call recall() before starting unfamiliar work. A hit with outcome 'failure' is "
        "a warning worth reading, not noise -- it is the one thing a freshly spawned "
        "agent cannot otherwise know.\n"
        "Call claim() before starting work on a shared resource, and release() as soon "
        "as you are done or have abandoned it. claim() returning a denial (a "
        "ClaimDenied) is a normal outcome, not an error: it names who holds the "
        "resource and what they intend, so you can wait, help, or pick different work "
        "instead of retrying blindly.\n"
        "Call remember() to write a trail once you have an outcome -- success, "
        "failure, abandoned, or inconclusive -- with the evidence for it. Trails are "
        "Roshambo's negative memory: failures are exactly as valuable to record as "
        "successes.\n"
        "Call decide() to log a choice with its rationale and provenance to the "
        "swarm-wide decision ledger. Call status() for a snapshot of the swarm.\n\n"
        "This server has no SQL tool by design. For schema introspection or ad-hoc "
        "analysis, use the CockroachDB Managed MCP Server instead."
    ),
)

# Lazily constructed on first tool call, not at import time, so this module -- and the
# tools it registers -- can be imported and listed even before roshambo.memory exists
# or a ROSHAMBO_DSN is configured.
_roshambo: Any = None
_import_error: Exception | None = None


def _get_roshambo() -> Any:
    """Return the shared ``Roshambo`` instance, constructing it on first use.

    Deferred and defensive on purpose: ``roshambo.config`` and ``roshambo.memory`` are owned
    by the Core lane (see ``CONTRACT.md``) and may not exist yet while this module is
    developed in parallel. Import failures are cached and re-raised as a clear
    ``RuntimeError`` so a tool call fails with an actionable message instead of a bare
    traceback, while ``list_tools()`` keeps working regardless.
    """
    global _roshambo, _import_error
    if _roshambo is not None:
        return _roshambo
    if _import_error is not None:
        raise RuntimeError(
            f"roshambo.memory is not available yet (Core lane): {_import_error!r}"
        ) from _import_error

    try:
        from roshambo.config import load_config
        from roshambo.memory import Roshambo
    except Exception as exc:  # pragma: no cover - only hit before Core lane lands
        _import_error = exc
        raise RuntimeError(
            f"roshambo.memory is not available yet (Core lane): {exc!r}"
        ) from exc

    # Passing embedder=None lets Roshambo itself decide: roshambo.embeddings.get_embedder
    # (AWS lane) if importable, otherwise memory.PlaceholderEmbedder. This server does
    # not duplicate that fallback -- Core owns it, per CONTRACT.md.
    cfg = load_config(os.environ)
    _roshambo = Roshambo(cfg)
    return _roshambo


def _serialize(value: Any) -> Any:
    """Convert a value from ``roshambo.models`` into JSON-safe data for an MCP tool result.

    ``roshambo.models`` types are plain frozen dataclasses (``Claim``, ``ClaimDenied``,
    ``Trail``, ``RecallHit``, ``Fact``, ``Decision``, ``SwarmStatus``, per
    ``CONTRACT.md``). Each becomes a dict with a ``_type`` field carrying the class
    name, so a caller can tell a ``Claim`` from a ``ClaimDenied`` -- the distinction
    ``claim()`` relies on to signal "denied", not "errored".
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, Any] = {"_type": type(value).__name__}
        for field in dataclasses.fields(value):
            out[field.name] = _serialize(getattr(value, field.name))
        return out
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


@mcp.tool()
def register_agent(
    agent_id: str,
    framework: str,
    host: str,
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Register a stable agent identity before claiming work.

    `agent_id` must be collision-safe across machines (for example
    `codex-1@workstation-a`). Re-registering the same id updates the current registry
    row, while audit events retain the framework/host snapshot captured when they were
    written.
    """
    registered = _get_roshambo().register_agent(
        agent_id=agent_id,
        framework=framework,
        host=host,
        capabilities=capabilities,
    )
    return {
        "registered": True,
        "agent_id": registered,
        "framework": framework,
        "host": host,
    }


@mcp.tool()
def claim(
    resource: str,
    agent_id: str,
    intent: str,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Acquire an exclusive, serializable lease on `resource` for `agent_id`.

    Call this before starting work another agent in the swarm could also attempt --
    for example before editing a specific file or picking up a specific task. The
    result's `_type` field is either "Claim" (you got it) or "ClaimDenied" (someone
    else holds it right now; the result names who, and their stated intent, so you can
    decide whether to wait, help, or do something else). A denial is a normal,
    expected outcome, not a failure to retry blindly.
    """
    return _serialize(
        _get_roshambo().claim(
            resource=resource, agent_id=agent_id, intent=intent, ttl_seconds=ttl_seconds
        )
    )


@mcp.tool()
def heartbeat(claim_id: str) -> dict[str, Any]:
    """Extend a live lease after a concrete work step.

    A false result means the lease has expired or changed hands and is deliberately
    not renewable. Stop work rather than retrying blindly. Heartbeats should represent
    progress, not a background timer that keeps a stuck agent alive.
    """
    alive = _get_roshambo().heartbeat(claim_id=claim_id)
    return {"claim_id": claim_id, "alive": alive}


@mcp.tool()
def release(claim_id: str) -> dict[str, Any]:
    """Release a claim so another agent can pick up its resource.

    Call this as soon as claimed work is finished or abandoned. An expired but
    unreleased claim still blocks other agents until its TTL elapses, so release
    promptly rather than relying on expiry.
    """
    released = _get_roshambo().release(claim_id=claim_id)
    return {"claim_id": claim_id, "released": released}


@mcp.tool()
def remember(
    topic: str,
    approach: str,
    outcome: str,
    evidence: str,
    agent_id: str | None = None,
    detail: dict[str, Any] | None = None,
    artifact_uri: str | None = None,
) -> dict[str, Any]:
    """Record what was tried and how it ended -- Roshambo's negative memory.

    `outcome` must be one of: "success", "failure", "abandoned", "inconclusive".
    `evidence` should be concrete: an error message, a measurement, a quote -- whatever
    justifies the outcome to a reader with no other context. Write a trail even for
    failures and abandoned attempts: that is what lets a later, unrelated agent avoid
    repeating a dead end it has no way of otherwise knowing about. `artifact_uri` may
    point at a larger attachment (for example an `s3://` URI) instead of inlining it.
    """
    return _serialize(
        _get_roshambo().remember(
            topic=topic,
            approach=approach,
            outcome=outcome,
            evidence=evidence,
            agent_id=agent_id,
            detail=detail,
            artifact_uri=artifact_uri,
        )
    )


@mcp.tool()
def recall(
    query: str,
    limit: int = 5,
    outcomes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Semantically search past trails before starting unfamiliar work.

    Call this *first*, before claim(), whenever a task is not obviously routine.
    `query` does not need to match prior wording -- this is a vector search over
    `topic`/`approach`/`evidence`, so a differently-phrased description of the same
    underlying attempt can still surface it. `outcomes` optionally restricts results to
    specific outcome values (for example `["failure", "abandoned"]` to search only for
    prior dead ends). Each hit carries the matched trail plus a distance and a
    `strength` (trails that keep getting reconfirmed rank higher over time).
    """
    hits = _get_roshambo().recall(query=query, limit=limit, outcomes=outcomes)
    return [_serialize(hit) for hit in hits]


@mcp.tool()
def decide(
    question: str,
    choice: str,
    rationale: str,
    confidence: str,
    provenance: str,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Log a decision with its rationale and provenance to the swarm-wide ledger.

    `confidence` must be one of: "high", "medium", "low". `provenance` must be one of:
    "agent-inferred" (you decided this on your own), "human-confirmed" (a human signed
    off on it), or "human-corrected" (a human overrode a prior agent decision) -- use
    "agent-inferred" unless a human was actually in the loop for this specific
    decision. Prefer decide() over an ad-hoc remember() call whenever the point is the
    choice itself, not an attempt's outcome.
    """
    return _serialize(
        _get_roshambo().decide(
            question=question,
            choice=choice,
            rationale=rationale,
            confidence=confidence,
            provenance=provenance,
            agent_id=agent_id,
        )
    )


@mcp.tool()
def status() -> dict[str, Any]:
    """Return a snapshot of the swarm: agent count, active claims, trails, and facts.

    Useful as a cheap sanity check before a multi-agent run, or to explain to a human
    what the swarm is currently doing.
    """
    return _serialize(_get_roshambo().status())


def main() -> None:
    """Entry point for the `roshambo-mcp` console script.

    Runs over stdio by default (`FastMCP.run()`), which is what Claude Code and other
    local MCP clients expect from a process they launch directly, per
    `pyproject.toml`'s `[project.scripts]` entry `roshambo-mcp = "roshambo.mcp.server:main"`.
    """
    mcp.run()


if __name__ == "__main__":
    main()
