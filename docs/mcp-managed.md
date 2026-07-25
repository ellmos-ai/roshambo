# The CockroachDB Cloud Managed MCP Server

This document explains the *other* MCP path in Roshambo's architecture: the
CockroachDB Cloud **Managed MCP Server**, not `roshambo-mcp` (this repository's own
server, see the top-level README and `src/roshambo/mcp/server.py`). The two are
deliberately separate — see "Why two MCP servers" below.

## What it is

The Managed MCP Server is a fully hosted MCP endpoint operated by CockroachDB Cloud, at:

```
https://cockroachlabs.cloud/mcp
```

It lets an AI agent or tool (Claude Code, Cursor, VS Code, and others) list databases
and tables, describe schemas and indexes, inspect cluster health and running queries,
and run read-only SQL and `EXPLAIN` statements against a CockroachDB Cloud cluster —
without you deploying or operating any proxy or bridge yourself. With write access
explicitly granted, it can also create databases, create tables, and insert rows;
destructive operations such as `DROP` or `TRUNCATE` remain unsupported even then.

## Authentication

Two authentication mechanisms are supported:

- **OAuth 2.1 (Authorization Code flow with PKCE)** — for interactive, human-in-the-loop
  workflows. Permissions are scoped during the consent screen.
- **Service account API keys** — for fully autonomous / non-interactive environments
  (for example a CI job or an unattended agent). A service account can be granted
  CockroachDB Cloud RBAC roles scoped to specific clusters.

## Scopes

- **`mcp:read`** — only safe, introspective tools are permitted (for example
  `list_databases`, `select_query`, `get_table_schema`).
- **`mcp:write`** — additionally enables tools such as `create_database`,
  `create_table`, `insert_rows`. Destructive SQL (`DROP`, `TRUNCATE`, ...) stays
  unsupported regardless of scope.

**Read-only is the default.** Write access is opt-in via explicit consent, so an agent
can explore schemas, inspect query plans, and run analytical queries without risking an
unintended write. This default is why Roshambo treats the Managed MCP Server purely as
an *inspection* path, not as part of its own write path (see below).

## Observability

CockroachDB Cloud logs every call through the Managed MCP Server: tool name, cluster
context, a redacted form of the SQL shape, latency, and response size, integrated with
CockroachDB Cloud's own observability pipeline. This is a second, independent audit
trail alongside Roshambo's own `audit_log` table (see the top-level README's "Security"
section) — useful precisely because it comes from a different system than Roshambo's
own code.

## Getting your own connection snippet

CockroachDB Cloud generates the exact client configuration snippet for you, in the
Cloud Console, scoped to your own cluster and credentials. **This repository
deliberately does not hard-code or invent that snippet** — a generic example here could
be wrong for your cluster, or silently go stale if the console's format changes. To get
it:

1. Open your cluster in the CockroachDB Cloud Console.
2. Navigate to the MCP / AI agent connection settings for that cluster.
3. Choose OAuth (for an interactive session) or generate a service account API key (for
   an unattended agent), and choose `mcp:read` or `mcp:read` + `mcp:write` as your
   situation requires — default to read-only unless you specifically need the agent to
   write.
4. Copy the snippet the console gives you.

## Connecting it to Claude Code

Once you have a snippet from the console, the general shape of wiring *any* remote HTTP
MCP server into Claude Code is (this part is Claude Code's own documented mechanism,
not CockroachDB-specific — see
[code.claude.com/docs/en/mcp](https://code.claude.com/docs/en/mcp)):

```bash
# Add the server (name is yours to choose)
claude mcp add --transport http cockroachdb-managed https://cockroachlabs.cloud/mcp

# Then, inside a Claude Code session, complete the OAuth 2.1 / PKCE login:
/mcp
```

If your console snippet instead gives you a service account API key rather than an
OAuth flow, pass it as an HTTP header at add-time instead of using `/mcp` to log in —
again, the general Claude Code mechanism (confirm the exact header name and scheme the
Console tells you to use; do not assume it matches this example):

```bash
claude mcp add --transport http cockroachdb-managed https://cockroachlabs.cloud/mcp \
  --header "Authorization: Bearer <your-service-account-api-key>"
```

Either way, verify the connection from inside Claude Code with `/mcp` — a connected
server shows its tool count there.

## Why two MCP servers

Roshambo deliberately keeps two MCP paths onto the same CockroachDB cluster apart:

- The **Managed MCP Server** (this document) is the human-adjacent inspection path:
  schema introspection, ad-hoc analysis, operational questions. Read-only by default.
- **`roshambo-mcp`** (this repository's own server, see the top-level README) is the
  agent-adjacent path: a narrow, checked set of six verbs (`claim`, `release`,
  `remember`, `recall`, `decide`, `status`) that enforce Roshambo's invariants. It has
  **no SQL tool at all** — an agent that could write arbitrary SQL could violate those
  invariants (release a lease it does not hold, write a trail with no evidence, skip
  the mandatory `provenance` on a decision).

If you want an agent to explore Roshambo's own tables directly (for debugging, or to
understand the schema), point it at the Managed MCP Server in read-only mode. If you
want an agent to actually *use* Roshambo's memory correctly, point it at `roshambo-mcp`
and the Agent Skills in `skills/` (see `docs/skills.md`).
