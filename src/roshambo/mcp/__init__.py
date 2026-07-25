"""roshambo.mcp -- the agent-facing MCP server for Roshambo.

Exposes the ``roshambo-mcp`` console script (see ``pyproject.toml``): a narrow, six-verb
MCP surface (claim, release, remember, recall, decide, status) backed by
``roshambo.memory.Roshambo``. See ``server`` for the implementation and
``docs/mcp-managed.md`` for how this relates to the CockroachDB Managed MCP Server.
"""

from .server import main, mcp

__all__ = ["main", "mcp"]
