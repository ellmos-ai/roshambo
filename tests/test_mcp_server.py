"""Tests for roshambo-mcp: tool registration, and -- where a live cluster is configured --
a full round trip through the actual `call_tool()` path a real MCP client would use.

No cloud credentials are required for this file to produce a green run: only
`test_full_round_trip_through_call_tool` needs a real cluster, and it is marked `live`
so it skips cleanly without `ROSHAMBO_DSN`, matching the convention in `tests/conftest.py`.
See `docs/EVIDENCE-iface.md` for an executed, non-skipped run of the whole file,
including that live test.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from roshambo.mcp.server import mcp

EXPECTED_TOOLS = {"claim", "release", "remember", "recall", "decide", "status"}


def _list_tools():
    return asyncio.run(mcp.list_tools())


def test_lists_exactly_the_six_contracted_tools():
    """The hard requirement: roshambo-mcp exposes claim/release/remember/recall/decide/status
    and nothing else."""
    names = {tool.name for tool in _list_tools()}
    assert names == EXPECTED_TOOLS


def test_every_tool_has_a_substantial_description():
    """Descriptions are what an agent reads before deciding whether/how to call a tool --
    a one-liner is not enough guidance, e.g. for the claim()-vs-ClaimDenied distinction."""
    for tool in _list_tools():
        assert tool.description and len(tool.description) > 40


@pytest.mark.parametrize(
    ("tool_name", "required"),
    [
        ("claim", {"resource", "agent_id", "intent"}),
        ("release", {"claim_id"}),
        ("remember", {"topic", "approach", "outcome", "evidence"}),
        ("recall", {"query"}),
        ("decide", {"question", "choice", "rationale", "confidence", "provenance"}),
        ("status", set()),
    ],
)
def test_tool_input_schemas_match_the_contracted_signature(tool_name, required):
    """Required parameters must match roshambo.memory.Roshambo's frozen signatures from
    CONTRACT.md -- this is what would break silently if a wrapper drifted from Core."""
    tools = {tool.name: tool for tool in _list_tools()}
    schema = tools[tool_name].inputSchema
    assert set(schema.get("required", [])) == required


def test_no_tool_accepts_free_form_sql():
    """The deliberate security boundary from CONTRACT.md / docs/mcp-managed.md: this
    server has no SQL escape hatch. Only recall()'s `query` is a free-text argument, and
    it is embedded and vector-searched, never executed as SQL."""
    for tool in _list_tools():
        properties = tool.inputSchema.get("properties", {})
        assert "sql" not in properties
        assert "statement" not in properties
        if "query" in properties:
            assert tool.name == "recall"


def test_calling_a_tool_without_roshambo_dsn_fails_clearly(monkeypatch):
    """No live cluster required: exercises the config-error path a user hits if they
    launch roshambo-mcp before setting ROSHAMBO_DSN. Must fail with a message naming the
    actual problem, not a bare traceback from a missing attribute somewhere downstream."""
    monkeypatch.delenv("ROSHAMBO_DSN", raising=False)
    import roshambo.mcp.server as server_module

    server_module._roshambo = None
    server_module._import_error = None

    with pytest.raises(ToolError, match="ROSHAMBO_DSN"):
        asyncio.run(mcp.call_tool("status", {}))


@pytest.mark.live
def test_full_round_trip_through_call_tool(monkeypatch, live_dsn, schema_ready, swarm_id):
    """End-to-end proof against a real CockroachDB cluster, through mcp.call_tool() --
    the same entry point a real MCP client (e.g. Claude Code) uses -- not by calling
    roshambo.memory.Roshambo directly. Exercises all six tools in one coherent story: claim a
    resource, hit a dead end, recall it from a differently-worded query, decide how to
    proceed, check status, release the lease.
    """
    monkeypatch.setenv("ROSHAMBO_DSN", live_dsn)
    monkeypatch.setenv("ROSHAMBO_SWARM_ID", swarm_id)
    # The server builds its Roshambo from the environment. Without AWS credentials the
    # default ("bedrock") would fail inside `remember` — use the offline embedder,
    # clearly non-semantic, which is irrelevant here: this test proves the MCP
    # round-trip, not embedding quality.
    monkeypatch.setenv("ROSHAMBO_EMBEDDING_PROVIDER", "local")
    import roshambo.mcp.server as server_module

    server_module._roshambo = None
    server_module._import_error = None

    def _call_blocks(name: str, arguments: dict) -> list:
        result = asyncio.run(mcp.call_tool(name, arguments))
        # mcp >= 1.10 returns (content_blocks, structured_output); older versions
        # return the content block list directly. Accept both shapes.
        content = result[0] if isinstance(result, tuple) else result
        return [json.loads(block.text) for block in content]

    def _call(name: str, arguments: dict):
        return _call_blocks(name, arguments)[0]

    claimed = _call(
        "claim",
        {"resource": "repo:roshambo:demo-task", "agent_id": "agent-a", "intent": "try approach X"},
    )
    assert claimed["_type"] == "Claim"

    denied = _call(
        "claim",
        {
            "resource": "repo:roshambo:demo-task",
            "agent_id": "agent-b",
            "intent": "also try approach X",
        },
    )
    assert denied["_type"] == "ClaimDenied"
    assert denied["held_by"] == "agent-a"

    trail = _call(
        "remember",
        {
            "topic": "demo-task",
            "approach": "approach X: brute force",
            "outcome": "failure",
            "evidence": "timed out after 30s, dataset too large for this approach",
            "agent_id": "agent-a",
        },
    )
    assert trail["_type"] == "Trail"
    assert trail["outcome"] == "failure"

    # recall returns a list; FastMCP renders one content block per element.
    hits = _call_blocks("recall", {"query": "brute forcing the demo task timed out", "limit": 3})
    assert any(hit["trail"]["trail_id"] == trail["trail_id"] for hit in hits)

    decision = _call(
        "decide",
        {
            "question": "how to approach demo-task after approach X failed",
            "choice": "switch to an indexed approach",
            "rationale": "approach X timed out on this dataset size per the recalled trail",
            "confidence": "medium",
            "provenance": "agent-inferred",
            "agent_id": "agent-a",
        },
    )
    assert decision["_type"] == "Decision"

    snapshot = _call("status", {})
    assert snapshot["_type"] == "SwarmStatus"
    assert snapshot["trails"] >= 1

    released = _call("release", {"claim_id": claimed["claim_id"]})
    assert released["released"] is True
