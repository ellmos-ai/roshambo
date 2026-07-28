# Roshambo

[![Tests](https://img.shields.io/badge/Tests-73%20passed%20%7C%2045%20skipped-success.svg)](https://github.com/ellmos-ai/roshambo)
[![Ecosystem](https://img.shields.io/badge/ellmos--ai-framework-blue.svg)](https://github.com/ellmos-ai)
[![Umbrella](https://img.shields.io/badge/open--bricks-umbrella-teal.svg)](https://github.com/open-bricks)
[![LLM-Ready](https://img.shields.io/badge/LLM-Ready-brightgreen.svg)](llms.txt)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

**the multi-agent coordinator**

**[Deutsche Fassung / German version: README_de.md](README_de.md)**

![Roshambo mark: a teal triangle connected above a grey circle and a grey square on a dark background, converging on one point — one shape set apart as the winner](assets/roshambo-banner-v2.png)

> [!NOTE]
> **AI Agent Integration & LLM Discovery**: Roshambo provides native MCP integration (`roshambo-mcp`) and structured memory interfaces. Read [`llms.txt`](llms.txt) for machine-readable context, architectural details, and verification specifications.

**Three agents throw at the same time. Exactly one wins.
Not by luck — by a serializable transaction.**

Roshambo is the English name for the game of rock-paper-scissors: everyone throws at
the same time, and exactly one throw wins. That is the shape of the problem this
project solves for agent swarms — except here, the winner is not decided by chance, it
is decided by a serializable transaction in CockroachDB.

```mermaid
graph TD
    SubagentA[Agent Alpha] -->|1. Claim Task Lease| RoshamboClient[Roshambo Core Client]
    SubagentB[Agent Beta] -->|1. Claim Task Lease| RoshamboClient
    RoshamboClient -->|Serializable Tx| CDB[(CockroachDB Cluster)]
    CDB -->|Lease Granted| SubagentA
    CDB -->|Conflict Error| SubagentB
    SubagentA -->|2. Record Attempt Outcome| MemoryFabric[Negative Memory & Trails]
    MemoryFabric -->|Embed via Titan V2| VectorIdx[Distributed Vector Index]
    SubagentB -->|3. Query Prior Failures| VectorIdx
```

> Roshambo is a multi-agent coordinator. CockroachDB is the system of record:
> serializable leases so two agents never claim the same work, and a distributed
> vector index so an agent can ask, before it starts, "has anyone tried this — and
> how did it end?"


Roshambo's second characteristic, alongside coordination, is **negative memory**: it
does not primarily store documents or conversations, it stores the *outcomes of
attempts* — failures included — and makes it possible to find the earlier attempt again
later, even when a new query is worded differently than the original entry. A human
remembers their own dead ends; a freshly spawned agent does not, unless something wrote
it down. (This is vector search over embeddings — see [Known limitations](#known-limitations)
for exactly what has and has not been verified about how well it captures meaning versus
vocabulary.)

Built for the [CockroachDB x AWS Hackathon: Build with Agentic Memory](https://cockroachdb-ai.devpost.com/)
(Cockroach Labs, managed by Devpost).

## Positioning: a multi-agent coordinator, not an agent-memory product

Agent memory is not an open field. Several well-built, well-documented systems already
cover it:

- **[Amazon Bedrock AgentCore Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html)**
  — a fully managed agent memory service: short-term event storage within a session,
  plus long-term memory extracted via configurable strategies (semantic, summarization,
  user-preference, episodic), retrievable by semantic search across sessions.
- **[`langchain-cockroachdb`](https://github.com/cockroachdb/langchain-cockroachdb)**
  (official) — a vector store and a LangGraph checkpointer (`CockroachDBSaver`,
  `AsyncCockroachDBSaver`) for thread-scoped agent state, on the same database Roshambo
  uses.
- **[Memori Labs × CockroachDB](https://www.cockroachlabs.com/blog/agent-memory-database-cockroachdb-memori/)**
  — a memory layer for agent facts, events, and embeddings, also built on CockroachDB.
- **[Amazon Bedrock Multi-Agent Collaboration](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-multi-agent-collaboration.html)**
  — a supervisor pattern for up to 10 collaborator agents within one Bedrock account,
  with `conversationHistory` sharing between supervisor and collaborators.
- **[Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams)** — a shared
  task list, peer-to-peer messages, and file locking between teammates in one Claude
  Code session.

Roshambo does not compete with any of these on their own ground, and does not rebuild
what they already do well. **Roshambo is a multi-agent coordinator: the coordination
layer between agents that do not know about each other** — different vendors, different
machines, different sessions — plus the memory of how their attempts turned out.

| Property | Agent Teams | Bedrock Multi-Agent Collaboration | AgentCore Memory | Roshambo |
|---|---|---|---|---|
| Reach | one session, one process, one vendor | Bedrock agents inside one account | agents on AgentCore | cross-vendor, cross-machine |
| Coordination lifetime | ends with the session | conversation context | — | durable in the database, survives a process crash |
| What is coordinated | files in the team | tasks, supervisor to collaborator | — | any named resource (repos, files, cloud resources, datasets) |
| "Who is working on what", asked from outside | team-internal | team-internal | — | anyone involved can ask, including a human |
| Memory of attempt outcomes | no | no | conversation extraction | yes, retrievable by vector search even when reworded, failures included |

Honesty about the boundary: if what you actually need is conversation memory or
thread-scoped agent state, use the official integrations above — Roshambo does not
reimplement them, and it is complementary to them, not a replacement (the same
CockroachDB cluster can serve both at once).

**Why this gap stays open:** no vendor has an incentive to build coordination for its
competitors' agents. Agent Teams coordinates Claude Code sessions; Bedrock Multi-Agent
Collaboration coordinates Bedrock agents. Common practice, though, is to run several of
these tools side by side on the same project. That space between them — where an agent
built by one vendor needs to know what an agent built by another vendor already tried —
is the space Roshambo occupies.

## Coordination through shared state, not through a protocol

Roshambo has no message format that two agents must both implement. Everything one agent
knows about another, it reads out of the database. That is not a gap in the design, it is
the reason it works across vendors: **a protocol needs both sides to speak it; shared
state only needs each side to be able to read it.** Nobody has to agree with anybody.

**This does not exclude messages.** A message written as a column on a claim row is still
shared state. The file-based team-lock procedure we run in production already works
exactly that way — its lock template carries a `Queue:` field for waiting agents or
follow-on claims, and a `Notes:` field for "a short message, lessons learned or a
handover". So you can have messages without having a message protocol. Roshambo's schema
today carries `intent` on a claim and outcomes in `trails`; a waiting queue and a note
field would be a small addition to an existing table, not a new mechanism.

That gives one tool at two levels:

- **Agents that have never met** — bare `claim`/`release` plus `trails`. No agreement
  needed; it works with anything that can reach the database.
- **A team that knows about each other** — the same table, plus a queue and a note on the
  claim. Handovers and "I am waiting for X" become possible.

### Who locks the lock file?

Any file-based lock has to protect the lock file itself, and that regress has to end
somewhere. In the file world it ends at `O_EXCL`: an atomic create supplied by the
operating system. Our own lock module states the boundary plainly — atomic claim
assignment via `O_EXCL` is "the race-safe *I am taking this work package* that a pure
file format cannot provide."

But `O_EXCL` is atomic **locally**. Across a synchronised folder it does not hold, and
that is not hypothetical here: on 2026-07-23 two hosts appended to the same shared
append-only log, the sync service could not merge and forked it instead, and each fork
held one unique line missing from the canonical file — a textbook lost update, merged
back by hand. It is written up in this system's own operations log
(`AUFTRAG_ALLE-DESKTOP-APPS_KONFLIKTKOPIEN-WARTUNG_2026-07-27.md`); we cite it as a
documented incident rather than as something measured for this submission.

With a database the regress does not merely end lower, it disappears.
`INSERT … ON CONFLICT (swarm_id, resource)` against the primary key **is** the mutual
exclusion, supplied by the serializable transaction underneath. There is no "lock for the
claims table", because the atomicity does not live in the tool — it lives one layer below
it, in a layer every participant already reaches, and it holds across machines rather
than only within one.

### Assignment is not observance — and where observance can be enforced

The same lock module is careful about this and we keep its distinction: `O_EXCL` "makes
the *assignment* race-safe, not the *observance*." Roshambo inherits that distinction,
but not uniformly, and the difference is worth stating precisely because it is usually
overclaimed:

- **Resources outside the database** — files on a disk, cloud objects, an editor window.
  Here a claim is **advisory**. No coordinator can stop a process from writing to a file,
  and Roshambo does not pretend otherwise.
- **Resources inside the database** — `trails`, `decisions`, and messages if they are
  added. Here observance is **technically enforceable**, because the claim and the
  resource sit in the same transaction domain: a constraint, a trigger, or a
  `WHERE EXISTS (SELECT 1 FROM claims WHERE … AND expires_at > now())` on the write turns
  the advice into a rule. This is available rather than built — the current schema does
  not enforce it — and if a team-message table is ever added it should be claimed like
  any other resource rather than made an exception.

### Reach, not replacement

File-based team locks are **deliberately** scoped to one system: they coordinate the
agents of one machine and shut other machines out. That is not sloppiness, it is the
honest consequence of `O_EXCL` being locally atomic. Roshambo lifts exactly that
restriction. It does not replace the file approach; it extends its reach from one system
to any number.

## Why CockroachDB

The hackathon's central question is whether CockroachDB plays a meaningful,
production-grade role, not a swappable one. Roshambo needs two things at once, in
**one** database:

| Requirement | Why a vector store alone is not enough | Why a plain relational database alone is not enough |
|---|---|---|
| Two agents must never claim the same resource | Vector stores do not offer serializable transactions | — |
| "Has this been tried?" must be answered semantically | — | A database without a vector index only gives you full-text proximity |
| A claim (lease) and a memory (trail) must stay consistent with each other | A separate vector store creates exactly the "consistency gap" the hackathon brief warns about | Same problem, same direction |
| Agents spawn worldwide and write constantly | — | A single-region database becomes the failure point: "an agent whose memory goes offline doesn't degrade gracefully, it stops" |

That is a case that genuinely needs Serializable Isolation *and* a distributed vector
index together — not a case where CockroachDB is interchangeable with something else.

## Architecture

```mermaid
flowchart TB
    subgraph Agents["Agent swarm (heterogeneous)"]
        A1["Claude Code<br/>(local, MCP client)"]
        A2["Codex / Gemini / Kimi<br/>(local, MCP client)"]
        A3["Lambda worker<br/>(autonomous, spawned)"]
    end

    subgraph AWS["AWS"]
        L["AWS Lambda<br/>roshambo-worker"]
        B["Amazon Bedrock<br/>Titan embed v2 + Claude"]
        S3["Amazon S3<br/>artifacts, large payloads"]
        FG["ECS Fargate<br/>demo web app"]
    end

    subgraph CRDB["CockroachDB Cloud (system of record)"]
        T1["claims<br/>serializable leases"]
        T2["trails + VECTOR INDEX<br/>attempts &amp; outcomes"]
        T3["facts + VECTOR INDEX<br/>curated knowledge"]
        T4["decisions<br/>ledger with provenance"]
        T5["audit_log<br/>append-only"]
    end

    MCPO["CockroachDB Managed MCP Server<br/>cockroachlabs.cloud/mcp"]
    MCPC["roshambo-mcp<br/>(this repository's MCP server)"]

    A1 --> MCPC
    A2 --> MCPC
    A1 -. "schema introspection, read-only" .-> MCPO
    A3 --> L
    MCPC --> CRDB
    MCPO --> CRDB
    L --> CRDB
    L --> B
    L --> S3
    MCPC --> B
    FG --> CRDB
    T2 -. "artifact_uri" .-> S3
```

Two MCP paths onto the same cluster, deliberately kept separate:

- **CockroachDB Managed MCP Server** (`https://cockroachlabs.cloud/mcp`) — the
  human-adjacent path: schema introspection, ad-hoc analysis, read-only by default.
  See [`docs/mcp-managed.md`](docs/mcp-managed.md).
- **`roshambo-mcp`** (this repository) — the agent-adjacent path: a narrow, checked set
  of eight verbs, no free-form SQL tool. See
  [Security](#security-no-free-form-sql-on-purpose) below.

## Status

This repository was built within a fixed hackathon submission window, with the core
data model, the AWS integration, and the agent interface developed in parallel as
separate workstreams. Each workstream keeps its own evidence log with commands that
were actually executed and their real output — read those before trusting a specific
claim anywhere in this README:

- Core data model, leases, recall: [`docs/EVIDENCE-core.md`](docs/EVIDENCE-core.md)
- AWS integration (Bedrock, Lambda, S3): [`docs/EVIDENCE-aws.md`](docs/EVIDENCE-aws.md)
- MCP server and Agent Skills: [`docs/EVIDENCE-iface.md`](docs/EVIDENCE-iface.md)

Implemented in this repository, with tests:

- CockroachDB schema (`claims`, `trails`, `facts`, `decisions`, `audit_log`) with a
  `VECTOR(1024)` index on `trails`/`facts`, prefixed by `swarm_id` — `schema/001_init.sql`
- The `Roshambo` client: `claim` / `release` / `remember` / `recall` / `decide` /
  `status`, plus `heartbeat`, `who_has`, `learn`, `reinforce` — `src/roshambo/memory.py`
- Embeddings: Amazon Titan Text Embeddings V2 via Bedrock, with an offline,
  explicitly-non-semantic fallback for development without AWS credentials —
  `src/roshambo/embeddings.py`
- `roshambo-worker`, an AWS Lambda handler implementing the claim -> recall -> work ->
  remember -> release cycle — `src/roshambo/aws/worker.py`
- S3 artifact storage for large trail/fact payloads — `src/roshambo/aws/s3.py`
- `roshambo-mcp`, the six-tool MCP server this document mostly describes —
  `src/roshambo/mcp/server.py`
- Agent Skills teaching an agent to use Roshambo correctly — `skills/`
- CI (`.github/workflows/ci.yml`): lint plus the credential-free test suite, on every
  push and pull request against `main`

Present in the tree and reported working by the AWS lane in direct testing, but not yet
written up in [`docs/EVIDENCE-aws.md`](docs/EVIDENCE-aws.md) as of this writing — treat
as believable, not yet as independently documented:

- Infrastructure-as-code / provisioning scripts (`infra/`) — Lambda packaging and
  deploy, IAM policy, `ccloud`-based cluster provisioning
- The demo web application (`demo/`) — a FastAPI service with a static frontend and a
  `mock`-mode fallback when no CockroachDB cluster is configured

Not yet started as of this writing:

- A hosted demo URL and the submission video

## Quickstart

Requires Python >= 3.10 and a reachable CockroachDB cluster (a local `cockroach demo`
/ `cockroach start-single-node` instance, or a CockroachDB Cloud cluster both work —
Roshambo only needs a standard PostgreSQL-wire DSN).

```bash
git clone <this-repository-url>
cd roshambo
pip install -e ".[dev]"          # add extras as needed: [aws] for boto3, [mcp] for the MCP server

export ROSHAMBO_DSN="postgresql://root@127.0.0.1:26257/roshambo?sslmode=disable"
export ROSHAMBO_SWARM_ID="demo"

roshambo init-schema     # creates tables and vector indexes; safe to re-run
roshambo status          # swarm=demo agents=0 active_claims=0 trails=0 failures=0 facts=0
```

Use `127.0.0.1`, not `localhost`, in the DSN: on at least one tested host `localhost`
resolved to `::1` first while a `start-single-node --listen-addr=localhost` cluster only
listens on `127.0.0.1`, adding a measured ~8 seconds of failed IPv6 handshake to every
connection (see [`docs/EVIDENCE-core.md`](docs/EVIDENCE-core.md)). Both addresses work;
one is far slower per connection.

`roshambo` is a deliberately narrow CLI over the same verbs as `roshambo-mcp` (`claim`,
`release`, `who-has`, `remember`, `recall`, plus `init-schema` and `status`) — see
`src/roshambo/cli.py`. There is no "run arbitrary SQL" subcommand there either.
`init-schema` also accepts `--repair-vector-indexes`, which rebuilds a vector index
whose operator class does not match what `recall()` queries with — needed only if
`trails`/`facts` were created by an older revision of `schema/001_init.sql`; see
[Known limitations](#known-limitations) for why that mismatch matters.

Run the MCP server directly (stdio transport):

```bash
roshambo-mcp
```

Connect it from Claude Code as a local stdio server, with the environment it needs:

```bash
claude mcp add --transport stdio roshambo \
  --env ROSHAMBO_DSN="postgresql://root@localhost:26257/roshambo?sslmode=disable" \
  --env ROSHAMBO_SWARM_ID="demo" \
  -- roshambo-mcp
```

Then, inside Claude Code, run `/mcp` to confirm it connected and lists eight tools.

## See it running

There is a small web app that shows a live swarm — who holds which lease, who was turned
away and by whom, and a `recall()` search box — plus a script that plays the four-part
demo scenario against a real cluster, one beat at a time:

```bash
pip install -r demo/requirements.txt
export ROSHAMBO_EMBEDDING_PROVIDER="placeholder"   # see the note below
python demo/serve.py --dev                          # http://127.0.0.1:8000/

python demo/run_story.py --beat 1   # three agents collide; one lease, two informed refusals
python demo/run_story.py --beat 2   # the winner hits a dead end and records it
python demo/run_story.py --beat 3   # a new session finds that failure and picks another route
python demo/run_story.py --beat 4   # a holder goes silent; the lease lapses and is taken over
```

Full walkthrough, including what to watch on screen during each beat:
[`demo/README.md`](demo/README.md). Screenshots of it running against a CockroachDB Cloud
cluster: [`docs/screenshots/`](docs/screenshots/).

The acceptance number for the coordination claim is measured, not asserted:
`python demo/run_story.py --measure --rounds 10` races three agents for one resource ten
times over and checks each round for exactly one winner, exactly two denials, and every
denial naming the actual winner. Ten of ten passed against the cloud cluster; three
different runtimes won. Method, numbers and what did *not* work are in
[`docs/EVIDENCE-demo.md`](docs/EVIDENCE-demo.md).

Two things worth knowing before you record or judge anything:

* The app **falls back to labelled mock data** whenever it cannot reach a cluster, rather
  than crashing. Check `curl http://127.0.0.1:8000/api/health` for `"mode":"live"` first;
  the page also shows a banner in mock mode.
* `ROSHAMBO_EMBEDDING_PROVIDER=placeholder` selects the offline embedder that ranks by
  **lexical overlap** (word tokens and character trigrams). It is the only offline
  embedder with usable retrieval signal, but it is not a semantic model — see
  [Known limitations](#known-limitations).

The same app also runs as an AWS Lambda function behind a Function URL — its intended
host — through one adapter, `demo.lambda_entry.handler`, with no second code path. That
is why the frontend polls rather than opening a WebSocket, which a Function URL cannot
do. It has not been deployed (no AWS account is attached yet); what is verified is that
the handler answers Function-URL-shaped events, binary assets and query strings included
(`tests/test_demo_lambda_entry.py`).

### One tool, two situations

Roshambo coordinates a team whose members know about each other, and it coordinates
agents that have never met. The second is the one no vendor builds for its competitors,
and the two are demonstrated differently on purpose:

**A team, in one process — the test you can run.** Everything above races three agent
runtimes inside a single process against a real cluster. It needs a database connection
and nothing else: no model API keys, no vendor accounts. That is what makes it the
acceptance test rather than a story — anyone judging this repository can run it.

**Strangers, across three vendors — the run we did.** Claude Code, OpenAI's Codex and
Google's Antigravity, each in its own operating-system process and its own fresh
session, were given the same ordered task list and told to take the first task that was
not done. They share no memory, no message bus and no file handle; everything one knows
about another it learned from the database. Real collisions followed, the audit log
recorded who was refused by whom, and a small Python package came out the other end.

Reproducing the second one needs accounts with three separate model vendors, so it is
offered as a demonstration rather than as homework. The apparatus is in
[`demo/multivendor/`](demo/multivendor/) and the measured result — including a first
attempt that produced no collisions at all, and why — is in
[`docs/EVIDENCE-multivendor.md`](docs/EVIDENCE-multivendor.md). The counting rules were
committed before the agents ran.

## Configuration

Everything is read from the environment under the `ROSHAMBO_` prefix
(`src/roshambo/config.py`), so the same configuration works in a shell, inside
`roshambo-mcp`, and inside a Lambda:

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `ROSHAMBO_DSN` | yes | — | PostgreSQL-wire connection string to CockroachDB |
| `ROSHAMBO_SWARM_ID` | no | `default` | Tenant/prefix key; the leading column of every table's primary key and of the vector index |
| `ROSHAMBO_EMBEDDING_DIM` | no | `1024` | Vector dimension (must match the schema's `VECTOR(n)` columns) |
| `ROSHAMBO_LEASE_TTL_SECONDS` | no | `300` | Default claim lifetime |
| `ROSHAMBO_EMBEDDING_PROVIDER` | no | `bedrock` | Which embedder to use: `bedrock` (real) or `local` (offline hash, no retrieval signal). `Roshambo(cfg)` additionally accepts `placeholder` for the lexical offline embedder; `roshambo.embeddings.get_embedder` — and therefore the Lambda worker — does not |
| `ROSHAMBO_AWS_REGION` | no | `us-east-1` | Region for Bedrock and S3 calls |
| `ROSHAMBO_BEDROCK_MODEL_ID` | no | `amazon.titan-embed-text-v2:0` | Bedrock embedding model |
| `ROSHAMBO_S3_BUCKET` | no | unset | Bucket for artifact storage; required only if you use `put_artifact` |

## The eight tools

`roshambo-mcp` exposes exactly these eight verbs — no more, and no free-form query tool
besides `recall`'s embedded vector search:

| Tool | Purpose |
|---|---|
| `register_agent(agent_id, framework, host, capabilities?)` | Bind a stable, host-qualified caller id to the registry before claiming work. Historical audit rows retain immutable framework/host snapshots if the registry later changes. |
| `claim(resource, agent_id, intent, ttl_seconds?)` | Take an exclusive, serializable lease. A denial (`ClaimDenied`) names who holds it and what they intend — it is a normal result, not an error to retry blindly. |
| `heartbeat(claim_id)` | Extend a still-live lease after concrete progress. `alive=false` means stop: an expired lease is never resurrected. |
| `release(claim_id)` | Free a claim so another agent can pick up the resource. |
| `remember(topic, approach, outcome, evidence, ...)` | Record what was tried and how it ended. `outcome` is one of `success` / `failure` / `abandoned` / `inconclusive` — failures are written exactly like successes. |
| `recall(query, limit?, outcomes?)` | Vector search over past trails — finds a prior attempt even when the query is worded differently than the original entry. Call this *before* `claim()` on anything not obviously routine. |
| `decide(question, choice, rationale, confidence, provenance, ...)` | Log a decision to the swarm-wide ledger. `provenance` is mandatory: whether a human was actually in the loop must never be guessed after the fact. |
| `status()` | A snapshot of the swarm: agent count, active claims, trails, failures, facts. |

## Which CockroachDB tool, for what

The hackathon asks submissions to use at least two of the four CockroachDB tools below
and to say what the agent actually did with them:

| CockroachDB tool | How Roshambo uses it | Where in this repository |
|---|---|---|
| **Distributed Vector Indexing** | `trails` and `facts` carry a `VECTOR(1024)` column with a `VECTOR INDEX` prefixed by `swarm_id`; `recall()` queries it with cosine distance (`<=>`) so an agent can check, before acting, whether an approach has already failed | `schema/001_init.sql`, `src/roshambo/memory.py` (`recall`), `tests/test_core_recall.py` |
| **CockroachDB Cloud Managed MCP Server** | The read-only inspection path for schema introspection and ad-hoc analysis, kept deliberately separate from `roshambo-mcp`'s narrow write path | [`docs/mcp-managed.md`](docs/mcp-managed.md) |
| **Agent Skills Repo** | Roshambo ships its own skills in the same `SKILL.md` format as `cockroachlabs/cockroachdb-skills`, and documents installing that repository alongside them | `skills/`, [`docs/skills.md`](docs/skills.md) |
| **ccloud CLI** | Planned: cluster, service-account, and backup provisioning driven from the agent side | `infra/` (see [Status](#status)) |

## Which AWS service, for what

| AWS service | How Roshambo uses it | Where in this repository |
|---|---|---|
| **Amazon Bedrock** | Titan Text Embeddings V2 (1024-dim) embeds every trail and fact before it is written to CockroachDB; an offline, explicitly-non-semantic embedder stands in when no AWS credentials are configured, so the rest of the system still runs | `src/roshambo/embeddings.py` |
| **AWS Lambda** | `roshambo-worker`: an autonomous handler that claims a resource, checks memory, does one unit of work, and writes back what happened — the "agents spawn autonomously and write constantly" half of the brief | `src/roshambo/aws/worker.py` |
| **Amazon S3** | Large trail/fact payloads are stored by `s3://` reference (`artifact_uri`) instead of inline in CockroachDB rows | `src/roshambo/aws/s3.py` |
| **Amazon ECS Fargate** *(optional)* | Planned hosting for the demo web application | `demo/` (see [Status](#status)) |

## Security: no free-form SQL, on purpose

`roshambo-mcp` does not expose a tool that accepts raw SQL, or even a generic "query"
argument beyond `recall`'s embedded vector search. That is a deliberate boundary, not
an oversight: an agent that can write arbitrary SQL can violate Roshambo's invariants —
releasing a lease it never held, writing a trail with no evidence, skipping the
mandatory `provenance` on a decision. Ad-hoc inspection and analytics belong to the
**CockroachDB Managed MCP Server** instead (read-only by default, full audit logging,
no custom proxy) — see [`docs/mcp-managed.md`](docs/mcp-managed.md). `roshambo-mcp`
stays the checked, narrow write path.

Every checked call is also appended to an append-only `audit_log` table. Its agent id
references the registry, while `framework_snapshot` and `host_snapshot` preserve the
identity observed when the event occurred even if the current registry entry changes.
See `Roshambo._audit` in `src/roshambo/memory.py`.

## Known limitations

- **Vectors are inserted one row at a time.** CockroachDB documents that batch inserts
  degrade vector index quality, so `remember()`/`learn()` have no bulk-insert variant.
- **`IMPORT INTO` is not supported on tables with a vector index**; any seed data goes
  through normal single-row inserts.
- **A vector index only accelerates queries filtered on its prefix column** — `recall()`
  therefore always filters on `swarm_id` first.
- **A vector index built with the wrong operator class silently stops being used.**
  `CREATE VECTOR INDEX` without an explicit operator class defaults to `vector_l2_ops`,
  which CockroachDB will not use for `recall()`'s cosine operator `<=>` — the query still
  returns correct rows, it just full-scans, invisibly, until someone runs `EXPLAIN` (see
  [`docs/feedback-to-cockroachlabs.md`](docs/feedback-to-cockroachlabs.md), point 1, and
  [`docs/EVIDENCE-core.md`](docs/EVIDENCE-core.md) for a run that hit exactly this on a
  table created by an older schema revision). `roshambo init-schema
  --repair-vector-indexes` detects and rebuilds a mismatched index; `apply_schema` now
  raises rather than finishing quietly when it finds one.
- **The offline `DeterministicEmbedder` is not a semantic model.** It exists so the rest
  of the system runs without AWS credentials, and it must never be mistaken for
  Bedrock's real embeddings in a demo or in results.
- **`recall()`'s tested retrieval so far is lexical, not semantic.** Against a real
  CockroachDB node, a rephrased query does find a stored failure again at rank one —
  but the evidence log traces that result to shared word and character-trigram overlap,
  not to any verified understanding of meaning (see
  [`docs/EVIDENCE-core.md`](docs/EVIDENCE-core.md)). The Amazon Titan embedding path
  that would make retrieval genuinely semantic is implemented but has not yet been run
  with AWS credentials in this environment (`test_recall_with_the_real_embedder` in
  `tests/test_core_recall.py`, currently skipped). Until that test has run, no claim in
  this document should be read as "recall understands meaning" — only "recall finds a
  prior entry even when it is worded differently."
- No performance or scale numbers are claimed in this README; see the evidence
  documents linked under [Status](#status) for whatever was actually measured.

## Agent Skills

`skills/` contains Roshambo's own [Agent Skills](https://github.com/cockroachlabs/cockroachdb-skills)
(`SKILL.md` format), teaching an agent the two habits Roshambo depends on: call
`recall()` before starting unfamiliar work, and follow claim/heartbeat/release lease
discipline. `heartbeat` is available through Python, CLI, and MCP.
[`docs/skills.md`](docs/skills.md) documents both skills and how to
additionally pull in `cockroachlabs/cockroachdb-skills` for general CockroachDB
operational knowledge.

## Development

```bash
pip install -e ".[dev]"
pytest                 # tests needing a live cluster are marked `live` and skip
                        # cleanly unless ROSHAMBO_DSN is set; see tests/conftest.py
ruff check .
```

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
