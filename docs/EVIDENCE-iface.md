# Evidence log — Interface lane (MCP server, Agent Skills, docs)

This file records commands actually executed against this repository, with their real
output, for the Interface lane's claims in `README.md` / `README_de.md`. Anything not in
this log should be treated as unverified by this lane. Environment: Windows 11,
Python 3.12.10, `pip`-installed dependencies (no virtualenv — see the note under
"Environment" below).

**Naming note:** this project was renamed from "Cairn" to "Roshambo" mid-build (decided
2026-07-25). Documentation (`README.md`, `README_de.md`, `docs/`, `skills/`) uses the new
name throughout. The Python package, module paths, and the MCP server's own runtime
identity are a centralized rename the orchestrator has not applied yet at the time this
evidence was collected — they still say `cairn` / `cairn-mcp` / `Cairn`, exactly as run.
The command transcripts below are left unedited to match what was actually executed;
where a transcript shows `cairn`, that is the current, real, pre-rename state of the
code, not a documentation inconsistency.

## Acceptance criterion: `LICENSE` is a complete, recognizable Apache-2.0 text

Read in full (`LICENSE`, 203 lines). It contains all nine numbered sections (Definitions
through Accepting Warranty or Additional Liability) and the Appendix with the standard
"Copyright 2026 the Roshambo authors" boilerplate — the complete, unmodified Apache
License 2.0 text, not an excerpt or a placeholder. `NOTICE` accompanies it and lists the
actual third-party runtime dependencies with their licenses (`mcp` — MIT, `psycopg` —
LGPL-3.0, `boto3`/`botocore` — Apache-2.0) and the one non-bundled, separately-installed
reference (`cockroachlabs/cockroachdb-skills`).

## Acceptance criterion: `roshambo-mcp` (currently `cairn-mcp`, see naming note above) starts and lists exactly six tools

Executed from the repository root:

```
PYTHONIOENCODING=utf-8 PYTHONPATH=src python -c "
import asyncio
from cairn.mcp.server import mcp

async def main():
    tools = await mcp.list_tools()
    print(f'Server name: {mcp.name}')
    print(f'Tool count: {len(tools)}')
    for t in tools:
        req = t.inputSchema.get('required', [])
        print(f'- {t.name}  required={req}')

asyncio.run(main())
"
```

Output (verbatim):

```
Server name: cairn-mcp
Tool count: 6
- claim  required=['resource', 'agent_id', 'intent']
- release  required=['claim_id']
- remember  required=['topic', 'approach', 'outcome', 'evidence']
- recall  required=['query']
- decide  required=['question', 'choice', 'rationale', 'confidence', 'provenance']
- status  required=[]
```

This starts the actual `FastMCP` server object from `src/cairn/mcp/server.py` and calls
its real `list_tools()` — the same call an MCP client makes over the stdio transport,
just made in-process instead of by spawning `cairn-mcp` as a subprocess and speaking the
JSON-RPC wire format by hand. `docs/HANDOFF.md` (if a note is needed) can request a
subprocess-level rerun from a lane with `claude mcp add` available end-to-end; the tool
identity, count, and required-argument sets are identical either way, since both paths
go through the same `FastMCP.list_tools()`.

## Test suite: `tests/test_mcp_server.py`

Executed from the repository root:

```
PYTHONIOENCODING=utf-8 PYTHONPATH=src python -m pytest tests/test_mcp_server.py -v
```

Output (verbatim, local absolute paths redacted to `<repo-root>` per this project's
"no absolute Windows paths" rule — nothing else in the output was altered):

```
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.0, pluggy-1.6.0 -- <python-install>\python.exe
cachedir: .pytest_cache
rootdir: <repo-root>
configfile: pyproject.toml
plugins: anyio-4.13.0, asyncio-1.4.0, timeout-2.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
timeout: 120.0s
timeout method: thread
timeout func_only: False
collecting ... collected 11 items

tests/test_mcp_server.py::test_lists_exactly_the_six_contracted_tools PASSED [  9%]
tests/test_mcp_server.py::test_every_tool_has_a_substantial_description PASSED [ 18%]
tests/test_mcp_server.py::test_tool_input_schemas_match_the_contracted_signature[claim-required0] PASSED [ 27%]
tests/test_mcp_server.py::test_tool_input_schemas_match_the_contracted_signature[release-required1] PASSED [ 36%]
tests/test_mcp_server.py::test_tool_input_schemas_match_the_contracted_signature[remember-required2] PASSED [ 45%]
tests/test_mcp_server.py::test_tool_input_schemas_match_the_contracted_signature[recall-required3] PASSED [ 54%]
tests/test_mcp_server.py::test_tool_input_schemas_match_the_contracted_signature[decide-required4] PASSED [ 63%]
tests/test_mcp_server.py::test_tool_input_schemas_match_the_contracted_signature[status-required5] PASSED [ 72%]
tests/test_mcp_server.py::test_no_tool_accepts_free_form_sql PASSED      [ 81%]
tests/test_mcp_server.py::test_calling_a_tool_without_cairn_dsn_fails_clearly PASSED [ 90%]
tests/test_mcp_server.py::test_full_round_trip_through_call_tool SKIPPED [100%]

======================== 10 passed, 1 skipped in 8.09s ========================
```

The one skip (`test_full_round_trip_through_call_tool`, marked `live`) requires
`CAIRN_DSN` pointed at a real CockroachDB cluster; no such cluster was reachable from
this environment when this evidence was collected. It is not counted as passing, and
this document does not claim it passed. Its assertions (a full claim → deny → remember
→ recall → decide → status → release cycle through `mcp.call_tool()`) are unexecuted
until someone reruns it against a live cluster.

## Lint

Executed from the repository root:

```
python -m ruff check src/cairn/mcp/ tests/test_mcp_server.py
```

Output: `All checks passed!` (after fixing two `E501` line-too-long violations in
`tests/test_mcp_server.py` introduced by this lane's own earlier edit — not present in
`src/cairn/mcp/server.py`, which was already clean).

## Environment note

`cairn.memory` (Core lane) imports `psycopg`, and `cairn.embeddings` (AWS lane) imports
`boto3`; both are required transitively just to import `cairn.mcp.server` because
`src/cairn/__init__.py` imports `Cairn` at package-import time. Neither was present in
this environment's Python installation at the start of this lane's work, which made
`tests/conftest.py` fail to import (`ModuleNotFoundError: No module named 'psycopg'`)
before any Interface-owned test could even collect. Fixed by installing the same extras
`pyproject.toml` already declares (`psycopg[binary]>=3.2`, `boto3>=1.35`,
`pytest-timeout>=2.3`) into the system Python rather than editing any Core- or AWS-owned
file — this is an environment/dependency gap, not a code defect in another lane, and
`pip install -e ".[dev]"` from a clean environment installs the same packages
automatically per `pyproject.toml`'s `dev` extra.

## Research sources for the README "Positioning" section

Every product named in `README.md` / `README_de.md`'s "Positioning" section was checked
against a current source before being described, per this project's "no invented facts"
rule. Sources used:

- Amazon Bedrock AgentCore Memory — short-term/long-term split, extraction strategies
  (semantic, summarization, user-preference, episodic):
  <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html> and
  <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/episodic-memory-strategy.html>
- `langchain-cockroachdb` — `CockroachDBSaver` / `AsyncCockroachDBSaver` LangGraph
  checkpointer: <https://github.com/cockroachdb/langchain-cockroachdb> and
  <https://docs.langchain.com/oss/python/integrations/providers/cockroachdb>
- Memori Labs × CockroachDB:
  <https://www.cockroachlabs.com/blog/agent-memory-database-cockroachdb-memori/>
  (published 2026-05-12, per the search result's own dateline)
- Amazon Bedrock Multi-Agent Collaboration — supervisor/collaborator pattern,
  `conversationHistory` sharing, and the "maximum of 10 collaborator agents per
  supervisor" quota specifically (verified against AWS's own quotas documentation, not
  assumed): <https://docs.aws.amazon.com/bedrock/latest/userguide/agents-multi-agent-collaboration.html>
  and <https://docs.aws.amazon.com/bedrock/latest/userguide/quotas.html>
- Claude Code Agent Teams — shared task list, peer-to-peer messaging, file locking:
  <https://code.claude.com/docs/en/agent-teams>
- `cockroachlabs/cockroachdb-skills` — nine skill categories (onboarding-and-migrations,
  application-development, performance-and-scaling, operations-and-lifecycle,
  resilience-and-disaster-recovery, observability-and-diagnostics,
  security-and-governance, integrations-and-ecosystem, cost-and-usage-management), none
  about multi-agent coordination: <https://github.com/cockroachlabs/cockroachdb-skills>

The "10 collaborators" figure specifically was not taken from the first search pass
(which did not surface a number) — it required a second, more targeted search against
AWS's quotas documentation before being included, precisely to avoid stating an
unverified number.

## Cross-lane request checked: `ClaimDenied` does not leak `claim_id`

`docs/HANDOFF.md` (Core lane, 2026-07-25) asked the interface lane to keep
`ClaimDenied` free of a `claim_id` field if the MCP server serializes claim results,
because handing the loser of a race the winner's `claim_id` would let it release a
lease it never held. Checked directly: `ClaimDenied` in `src/cairn/models.py` (fields
`resource`, `held_by`, `intent`, `expires_at` — no `claim_id`) and `_serialize()` in
`src/cairn/mcp/server.py`, which walks `dataclasses.fields()` and therefore can only
ever emit the fields a dataclass actually declares. A denied `claim()` call structurally
cannot include a `claim_id` in its MCP response; no code change was needed.

## Where this lane wrote "planned" instead of "built"

Per the project's no-overclaiming rule, the following are described in
`README.md` / `README_de.md` as planned/not-yet-verified rather than as done, because
this lane did not itself execute or verify them:

- **Anything in `infra/` or `demo/`** — owned by the AWS lane. At the time this document
  was written, `infra/deploy_lambda.py`, `infra/iam_*.json`, and `demo/app.py` /
  `demo/queries.py` / `demo/static/*` already existed in the tree (the AWS lane was
  actively building them concurrently with this lane's work), but this lane did not run
  or test them. The README's "Status" section defers to `docs/EVIDENCE-aws.md` as the
  authoritative source for what actually works there, rather than this lane guessing
  from file presence alone.
- **`docs/EVIDENCE-core.md`** and the live-cluster concurrency/recall runs it should
  contain — owned by the Core lane. Not written by this lane; the README points to it by
  name without asserting its contents.
- **CI (`.github/workflows/`)** — the directory exists but was empty at the time of
  writing; described as planned, not implemented.
- **The full `test_full_round_trip_through_call_tool` MCP round trip against a live
  CockroachDB cluster** — see "Test suite" above; skipped, not run, in this environment.
- **A specific skill count for `cockroachlabs/cockroachdb-skills`** — the repository's
  own page states "29+ other operational tasks" beyond its quick-start examples, but
  does not give an exact total skill count anywhere this lane could find; `docs/skills.md`
  and this document therefore say "dozens of individual skills" rather than inventing a
  precise number.
