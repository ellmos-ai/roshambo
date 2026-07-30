# Evidence — CockroachDB Cloud Managed MCP Server

Per CONTRACT.md ground rule 2 ("no overclaiming"): what ran for real is recorded with its
exact output, including a real attempt that did not fully succeed. This file is
specifically about the **Managed MCP Server** (`docs/mcp-managed.md`) — the read-only
inspection path, not `roshambo-mcp` (this repository's own server, covered elsewhere).

## What is verified: a real, authenticated transport-level connection

Connected for real, 2026-07-30, via the current official connection command (checked
against `github.com/cockroachdb/docs`'s own source via Context7 before running it, not
assumed from memory — the form in this repository's own `docs/mcp-managed.md` was
missing the `Authorization` header entirely):

```
claude mcp add cockroachdb-cloud https://cockroachlabs.cloud/mcp --transport http \
  --scope user \
  --header "mcp-cluster-id: 3d360126-b977-4537-ac43-e9b2c79f7e9c" \
  --header "Authorization: Bearer <service-account API key>"
```

(Cluster ID is not secret by itself — it identifies the cluster, not a credential. The
API key value is never written to this file; it lives in
`C:\_Local_DEV\CREDENTIALS\cockroachdb\cluster.md`, service account `roshambo-agent`.)

`claude mcp list` confirms a live, healthy HTTP connection, not just a saved config
entry:

```
cockroachdb-cloud: https://cockroachlabs.cloud/mcp (HTTP) - ✔ Connected
```

This is real: the server accepted the TLS connection and the header-based
authentication well enough to report itself healthy, which a wrong cluster ID or a
malformed/expired API key would not do (see the actual failure mode below, which is a
different, more specific error).

## What is NOT verified: any actual data access

Six read-only introspection/query steps were attempted, in order, from a fresh headless
Claude Code session (`claude -p ... --allowedTools "mcp__cockroachdb-cloud__*"
--dangerously-skip-permissions`) scoped to use *only* this MCP server's tools — no
write, insert, update, delete, or DDL was attempted or permitted. Verbatim results:

| Step | Tool called | Result |
|---|---|---|
| List databases | `mcp__cockroachdb-cloud__list_databases` | `MCP error 0: list databases: unauthorized` |
| List clusters (diagnostic, added by the agent itself when step 1 failed) | `mcp__cockroachdb-cloud__list_clusters` | `{"rows":[]}` — no error, but an **empty** cluster list |
| List tables | `mcp__cockroachdb-cloud__list_tables` (`database: "roshambo"`) | `MCP error 0: list tables: unauthorized` |
| Row count, `agents` | `mcp__cockroachdb-cloud__select_query` (`SELECT count(*) FROM agents`) | `MCP error 0: executing select query: unauthorized` |
| Row count, `claims` | `mcp__cockroachdb-cloud__select_query` (`SELECT count(*) FROM claims`) | `MCP error 0: executing select query: unauthorized` |
| Row count, `audit_log` | `mcp__cockroachdb-cloud__select_query` (`SELECT count(*) FROM audit_log`) | `MCP error 0: executing select query: unauthorized` |
| Schema of `trails` (to confirm the `VECTOR(1024)` column) | `mcp__cockroachdb-cloud__get_table_schema` (`database: "roshambo"`, `table: "trails"`) | `MCP error 0: get table schema: unauthorized` |

No table names, row counts, or schema details were confirmed. No number here is
estimated or inferred from other evidence files — every cell above is the tool's own
returned text.

## Diagnosis: transport auth succeeded, RBAC authorization did not

The pattern is specific enough to point at one cause rather than several possible ones:
the control-plane call (`list_clusters`) completes without error but returns zero
clusters, while every data-plane call against the (assumed) `roshambo` database fails
with the *same* `unauthorized`, not a "database/table not found" error. Both symptoms
point at the same thing: the `roshambo-agent` service account's API key authenticates
successfully (the connection itself would otherwise fail differently, e.g. at the HTTP/
TLS layer or with an auth-format error), but the account has **no CockroachDB Cloud RBAC
role granted on the `roshambo` cluster** — service account API keys are scoped by an
explicit role assignment in the Cloud Console, separate from creating the key itself
(`docs/mcp-managed.md`, "Authentication": *"A service account can be granted CockroachDB
Cloud RBAC roles scoped to specific clusters"* — key word "can be granted", i.e. not
automatic).

This is a Console-side account-administration action (assigning a role to the service
account on the specific cluster), not something fixable by retrying the same MCP call
with different parameters, and not something this session has the access or the
standing authorization to do itself. Stopped here per instruction rather than guessing
further or trying a different cluster ID.

## What would unblock this

1. In the CockroachDB Cloud Console, under the `roshambo-agent` service account (or the
   cluster's own access-management page), grant it a CockroachDB Cloud RBAC role scoped
   to the `roshambo` cluster (read access is sufficient for this use case — the Managed
   MCP Server itself defaults to `mcp:read`-scoped tools regardless).
2. Re-run the same six-step check unchanged — no code or connection-string change is
   expected to be needed if the diagnosis above is correct.
3. Alternative not attempted here: the OAuth 2.1/PKCE interactive login (`/mcp` inside an
   interactive Claude Code session) instead of the service-account API key. Not usable
   from this headless session (no browser/interactive consent available here), but worth
   trying if the Console role assignment is not immediately actionable — it authenticates
   as the human account owner directly rather than through the service account's grants.

## Status against the hackathon's "≥2 CockroachDB tools" requirement

Unchanged by this attempt: **Distributed Vector Indexing** remains the one tool with
full, run, evidenced usage (`docs/EVIDENCE-core.md`, `docs/EVIDENCE-cloud.md`). This
session adds a genuine, verified **connection** to the Managed MCP Server — not nothing,
and not fabricated — but not yet the "agent actually did something with our schema"
evidence the submission needs. See `EINREICHUNG-ENTWURF.md`'s CockroachDB-tools table for
how this is stated there.
