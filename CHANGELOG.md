# Changelog

All notable changes to this project are documented in this file. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project has not yet cut a
tagged git release (no `git tag`); the `[x.y.z]` headings below track `pyproject.toml`'s
`version` field instead, bumped on days with a coherent batch of changes.

## [Unreleased]

## [0.1.8] - 2026-08-01

### Fixed

- CI was red on every push since 2026-07-31 (10 consecutive runs on `main`, both the
  `lint (ruff)` and `test (no infra)` jobs), unnoticed because the submission deadline
  had passed and nobody was watching Actions. Two independent causes, both closed:
  - `mcp` was pinned as `mcp>=1.2` with no upper bound. `mcp` 2.0.0 (released
    2026-07-31) removed the `mcp.server.fastmcp.exceptions` module path that
    `tests/test_mcp_server.py` imports, so a fresh CI install picked up 2.0.0 and
    `pytest` failed at collection with `ModuleNotFoundError`, aborting the whole run
    (all three Python-version jobs, not just the MCP tests). Pinned to `mcp>=1.2,<2`
    in both `[project.optional-dependencies]` and `dev`, matching the 1.28.1 this
    project's suite is actually verified against; `src/roshambo/mcp/**`'s own
    2.x-compatibility is untouched and still open (noted in the pin's comment).
  - `ruff check .` failed with 28 findings (25 `E501` line-too-long, 3 `I001`
    unsorted-imports) in `demo/multivendor/bot_agent.py`, `demo/multivendor/run_field.py`,
    `demo/queries.py`, and `tests/test_bot_agent.py` -- files this project maintains
    (not the frozen `demo/multivendor/{fieldkit,starmap}-run/` field-run transcripts,
    which stay excluded on purpose). Reformatted only; no behaviour change. Offline
    suite still 140 passed / 51 skipped, `ruff check .` now clean.

## [0.1.7] - 2026-07-30

### Verified

- Re-audited the cross-host agent-id collision and empty-`agents`-table findings from
  `docs/NEXT-RUN.md` (2026-07-27 baseline) against current source, as requested for
  BUILD-1. Confirmed already resolved by 0.1.6, not newly fixed here: `register_agent`
  is reachable from both the CLI (`roshambo register-agent`) and the MCP server (tool
  `register_agent`, one of the eight contracted tools); `run_field.py` mints
  host-qualified ids (`{agent}@{host_label}`) behind a required `--host-label`; both
  multi-vendor prompts (`agent.md`, `starmap-agent.md`) call `register-agent` as step 0,
  before any claim; and `claims`/`audit_log` carry immutable `framework_snapshot` /
  `host_snapshot` columns plus a foreign key to `agents(swarm_id, agent_key)`, so an
  audit row is traceable to a host without a join. Eight tests in
  `tests/test_host_identity.py` cover this directly, including
  `test_cross_host_collision_requires_different_grant_and_denial_snapshots` and
  `test_schema_links_claim_and_audit_ids_to_registry_keys`; no new tests were added
  since the scenario BUILD-1 asked to guard is already pinned by name.
- Confirmed no remaining `cairn` occurrences outside the historical evidence
  transcripts (`docs/EVIDENCE-*.md`, `docs/HANDOFF.md`), which intentionally preserve
  pre-rename command output verbatim (see the naming note in `docs/EVIDENCE-cloud.md`
  and `docs/EVIDENCE-iface.md`). In particular `infra/deploy_lambda.py` and
  `demo/**` already use `roshambo-worker` throughout.

### Changed

- `run_field.py` and `collect_evidence.py` now default `--ttl` to 300 seconds instead of
  120. `docs/NEXT-RUN.md` measured task durations from the `starmap-2026-07-27` field run
  (median 82s, longest 355s, 5 of 11 tasks over the 120s TTL) and recommended 300s as the
  library's own default (`config.py`'s `DEFAULT_LEASE_TTL_SECONDS`), needing no separate
  justification and covering ten of the eleven measured tasks outright. The
  `demo/multivendor/README.md` usage example was updated to match; historical evidence
  (`docs/EVIDENCE-multivendor.md`, `PROTOCOL.md`, and the `fieldkit-run`/`starmap-run`
  artifacts) is left unchanged since it records what those past runs actually used.
- Updated `llms.txt` `Last-checked` timestamp to `2026-07-30` and updated test status verification metrics (110 passed, 51 skipped).
- Synchronized test status badges in `README.md` and `README_de.md` to reflect current pytest test suite metrics (110 passed, 51 skipped).
- `pyproject.toml` `version` raised from `0.1.0` to `0.1.7`, catching it up to the
  changelog history below, which had reached `[0.1.6]` while the packaged version
  field stayed at its initial value.

## [0.1.6] - 2026-07-28

### Added

- Registry-backed stable agent identities: a caller-selected `agent_key` is unique per
  swarm, while the original UUID remains the internal registry row id.
- Immutable `framework_snapshot` and `host_snapshot` evidence on claims and audit events,
  with foreign keys from caller-visible ids to the agent registry and an additive upgrade
  path for existing clusters.
- `register-agent` and `decide` CLI verbs; `register_agent` and `heartbeat` MCP tools.
- Required `--host-label` field-run identity, host-qualified agent ids, registration as
  the first prompt action, and database-derived cross-host collision/event evidence.

### Verification boundary

- Offline suite and static checks pass. A real two-machine field run was deliberately not
  fabricated and remains the external acceptance gate.

## [0.1.5] - 2026-07-27

### Added

- **`heartbeat` is reachable.** It has existed in `memory.py` since the initial release
  and the README prescribes claim/heartbeat/release discipline, but the verb was exposed
  on neither the CLI nor MCP — so no agent could follow that instruction. Every lease in
  the `starmap-2026-07-27` field run therefore ran on its TTL alone, and three lapsed
  mid-task and were re-granted. Now a CLI subcommand (`roshambo heartbeat <claim_id>`,
  exit 0 alive / 3 lapsed). MCP still lacks it, and the README now says so rather than
  leaving the same gap unmarked for MCP clients.

### Changed

- **`EXPIRED` splits what `NOOP` used to say at once** (`demo/multivendor/rsb.py`). A
  failed `release` meant either "already handed back" or "your lease lapsed and the work
  is somebody else's now"; two field agents read the second as the first and committed
  work that had been re-granted. The wrapper now takes `--resource`, looks up the current
  holder, and answers `EXPIRED held_by=… expires_at=… intent=…` — but only when somebody
  actually holds it. A genuinely free resource still answers `NOOP`; conflating the two
  would have swapped one misleading answer for another. `--resource` is required because
  `ACQUIRE_SQL` regenerates `claim_id` on takeover, so the old id identifies nothing.
- **The demo prompts anchor the heartbeat to finished work**, not to a timer: renewed by
  the clock a lease says "still running", renewed by progress it says "still getting
  somewhere". Both prompts now also state that a refused heartbeat means stop and report
  — without that, the new verb would be ignored exactly as `NOOP` was.

### Known limitations recorded

- `since=<takeover time>` is not reported: `Claim` carries no `acquired_at` and
  `HOLDER_SQL` does not select it. `expires_at` is shown instead and named as such,
  rather than widening the core model for one word of prose.
- Whether the change removes the re-grants is not yet measured — that needs a repeat
  field run. The remaining race (checking whether a file exists before writing it) is
  untouched and stays documented in `docs/EVIDENCE-multivendor.md`.

## [0.1.4] - 2026-07-27

### Added

- **Coordination through shared state, not through a protocol** (READMEs, both languages).
  Roshambo has no message format two agents must both implement, and that is the
  mechanism rather than a gap: a protocol needs both sides to speak it, shared state only
  needs each side to read it. It does not exclude messages — a note on a claim row is
  still shared state — so the two use cases are two levels of one tool: strangers use bare
  `claim`/`release` plus `trails`; a team that knows each other adds a queue and a note.
- **Where the lock-file regress ends.** Any file-based lock must protect its own lock
  file; in the file world that ends at `O_EXCL`, which is atomic only locally. With a
  database it disappears: `INSERT … ON CONFLICT (swarm_id, resource)` against the primary
  key *is* the mutual exclusion, one layer below the tool, and it holds across machines.
  Cited with a documented lost update from this system's operations log — as a documented
  incident, not as something measured for this submission.
- **Assignment is not observance, and where observance can be enforced.** Outside the
  database a claim is advisory. Inside it — `trails`, `decisions`, and messages if added —
  observance is technically enforceable, because claim and resource share a transaction
  domain. Stated as available, not as built.
- `demo/multivendor/starmap/`: a second joint project the agents build, this one visual.
  Data plus a deterministic renderer, so any past state can be rebuilt exactly;
  `timelapse.py` walks the git history and re-renders every commit into a numbered frame
  carrying the real commit timestamp. `tests/test_starmap_render.py` (15 tests) holds the
  two load-bearing properties: the renderer never fails, and two renders are byte-identical.
- `demo/multivendor/starmap-run/`: what three vendors built — ten constellations, two
  rendering modules, eight commits, eight frames. Kept verbatim, excluded from ruff.

### Measured

- Star map run: **32 cross-vendor contention events**, 47 genuine collisions on task
  resources, 47 of 47 denials naming the holder, **0 defects**, 0 stale. The git
  repository is reported apart as the serialization point (12 denials, 11 contention
  events after retries collapse).
- Capability landed where it was not assigned: all twelve tasks were claimable by anyone,
  and the projection went to OpenAI's agent — whose documented strength is formal accuracy
  — while the palette went to Anthropic's rather than Google's. One result in each
  direction, both reported.

### Corrected

- The first run's duplicated task was written up as "the holder had finished and
  released". The evidence never supported that: `release` is audited without its resource,
  so the log cannot distinguish a lease handed back from one that lapsed. An agent in the
  star map run recorded in its own `failure` trail that its 120-second lease expired
  mid-work and its task was re-claimed underneath it. **The duplicates in both runs are a
  lease shorter than the work** — a configuration finding, not a design one. Both
  write-ups now say so.
- `collect_evidence.py` only knew the fieldkit resource names, so the star map's
  collisions were landing in the catch-all bucket. Naming only; re-running the earlier
  swarm reproduced every published figure unchanged.
- One pre-registered definition in `PROTOCOL.md` was tautological and is corrected in a
  dated addendum rather than silently edited.

### Known limitations recorded

- **A commit is coarser than a claim.** `starmap:repo` was claimed eleven times and
  produced eight commits, because `git add -A` sweeps up other agents' finished work.
- Task 12 of the star map was never claimed; the run ended with work still on the list.
- Three vendors, but still **one machine**.

## [0.1.3] - 2026-07-27

### Added

- `demo/multivendor/`: apparatus for running coding agents from three different vendors
  — Claude Code (Anthropic), Codex (OpenAI) and Antigravity (Google) — against one shared
  task list, each in its own process and its own fresh session, coordinating through
  nothing but the database. `rsb.py` is the front door they call, `run_field.py` starts
  them, `collect_evidence.py` reads the result out of `audit_log`.
- `demo/multivendor/PROTOCOL.md`: the counting rules, committed **before** any agent ran,
  so the numbers could not be shaped after seeing the data. It also fixes what makes a
  run inconclusive rather than successful, and carries a dated addendum disclosing the
  one definition that had to be corrected.
- `docs/EVIDENCE-multivendor.md`: the measured result. **13 cross-vendor contention
  events** — thirteen times an agent from one vendor was refused work another vendor's
  agent held, and was told who held it. 28 genuine collisions on task resources in total,
  33 of 33 denials naming the holder, **0 defects**. The index serialization point is
  reported separately (4 collisions), never summed in.
- `demo/multivendor/fieldkit-run/`: what the three vendors actually built — twelve
  helper functions and their tests, 44 tests, 44 passed. Kept verbatim and excluded from
  ruff, because it is a record rather than a library.

### Changed

- `rsb.py` answers on stdout (`ROSHAMBO RESULT=GRANTED|DENIED|OK|NOOP|ERROR` as the first
  line) rather than relying on exit codes. Measured reason: asked to run a script exiting
  3 and report the code, the Antigravity agent reported 1. Each vendor drives a different
  shell, and a missing command also exits non-zero, so "claim refused" and "your shell
  could not find the wrapper" were the same signal. `roshambo.cli` is untouched — its
  output contract is frozen in `CONTRACT.md`.
- READMEs (both languages) now separate the two situations Roshambo covers: the
  in-process demo is the acceptance test a reader can run with only a database
  connection, and the three-vendor run is a demonstration, since repeating it needs
  accounts with three model vendors.

### Known limitations recorded

- **A lease says nobody is working on something; it does not say the work still needs
  doing.** Seen twice — once in the pilot, once in the measured run, where task 10 was
  granted a second time after its first holder had finished and released, leaving a
  duplicate line in the artifact's `INDEX.md`. Left in place rather than tidied.
- `decide` has no CLI subcommand, so it was not exercised.
- Three vendors, but **one machine**. Cross-machine coordination remains argued rather
  than measured, as does anything involving AWS.

## [0.1.2] - 2026-07-26

### Added

- `demo/lambda_entry.py`: the demo web app as an AWS Lambda Function URL handler, via one
  Mangum adapter over the same `demo.app:app` that runs locally — no second code path.
  Not deployed (no AWS account attached yet); `tests/test_demo_lambda_entry.py` drives the
  handler with payload-format-2.0 events and checks binary assets, query strings, the
  `FileResponse` index route and error status codes.

- `demo/run_story.py`: plays the four beats of the demo scenario against a live cluster,
  one at a time (`--beat N`) or in one go (`--all`). `--measure --rounds N` repeats the
  collision and judges each round against the phase-4 acceptance criterion — exactly one
  winner, exactly two denials, every denial naming the actual winner and its intent.
- `demo/serve.py`: one start command for the demo app with no hard-coded port or bind
  address (`ROSHAMBO_DEMO_HOST` / `ROSHAMBO_DEMO_PORT` / `PORT` /
  `ROSHAMBO_DEMO_ROOT_PATH`), so the eventual host does not force a code change.
- "Turned Away" panel and `GET /api/denials`: the agents that lost a race, each with the
  holder they were told about and that holder's intent. Read from the losers' own
  `outcome='abandoned'` trails (`demo/queries.py:recent_denials`), so the record survives
  the winner releasing its lease.
- Deep-linkable recall search (`/?query=…&outcomes=…&limit=…`): fills the form in and runs
  the search on load, so a result can be reproduced or recorded without typing.
- `docs/EVIDENCE-demo.md` and `docs/screenshots/`: the measured acceptance run (10 of 10
  rounds), the four beats as they actually ran, and four screenshots of the app live
  against a CockroachDB Cloud cluster.

### Fixed

- `demo/static/index.html` loaded `style.css` and `app.js` from the document root while
  both are served by the `/static` mount: the stylesheet and the entire frontend script
  returned 404. All page URLs are now relative, which also makes the app work behind a
  reverse proxy under a path prefix.

## [0.1.1] - 2026-07-26

### Added

- Add `llms.txt` index file with machine-readable metadata, verification status (73 passed, 45 skipped unit tests) and core module map.
- Add Shields.io status badges (Tests, Ecosystem, Umbrella, LLM-Ready, License) and GFM callout blocks (`> [!NOTE]`) to `README.md` and `README_de.md`.
- Add Mermaid.js system architecture diagram depicting multi-agent serializable lease acquisition and distributed vector memory index flow.

## [0.1.0] - 2026-07-25


Built for the [CockroachDB x AWS Hackathon: Build with Agentic Memory](https://cockroachdb-ai.devpost.com/).
See the top-level [`README.md`](README.md) ("Status") and the per-lane
`docs/EVIDENCE-*.md` files for exactly what was executed and verified, as opposed to
planned. This entry summarizes what exists in the repository, not what is claimed to
perform at any particular scale.

**Naming:** this project was renamed from "Roshambo" to "Roshambo" mid-build. Everything
below uses the new name; the underlying Python package and module paths
(`src/roshambo/...`) still carry the old name pending a centralized rename — see the
naming note at the top of [`docs/EVIDENCE-iface.md`](docs/EVIDENCE-iface.md).

### Added

- CockroachDB schema (`claims`, `trails`, `facts`, `decisions`, `audit_log`) with a
  `VECTOR(1024)` index on `trails` and `facts`, prefixed by `swarm_id`
  (`schema/001_init.sql`).
- The `Roshambo` client (`src/roshambo/memory.py`): `claim` / `release` / `remember` /
  `recall` / `decide` / `status`, plus `heartbeat`, `who_has`, `learn`, `reinforce`.
- Serializable lease acquisition via `INSERT ... ON CONFLICT ... DO UPDATE` — one
  atomic statement, no read-then-write race window (`src/roshambo/leases.py`).
- Embeddings via Amazon Titan Text Embeddings V2 (Bedrock), with an offline,
  explicitly-non-semantic fallback for development without AWS credentials
  (`src/roshambo/embeddings.py`). Retrieval verified so far is lexical, not semantic — see
  [`docs/EVIDENCE-core.md`](docs/EVIDENCE-core.md).
- `roshambo-worker`, an AWS Lambda handler implementing claim → recall → work →
  remember → release (`src/roshambo/aws/worker.py`).
- S3 artifact storage for large trail/fact payloads, referenced by `artifact_uri`
  (`src/roshambo/aws/s3.py`).
- `roshambo-mcp`: an MCP server (stdio transport, `mcp`'s `FastMCP`) exposing exactly
  six tools — `claim`, `release`, `remember`, `recall`, `decide`, `status` — and no
  free-form SQL tool (`src/roshambo/mcp/server.py`).
- `roshambo`, a CLI over the same verbs (`init-schema`, `status`, `claim`, `release`,
  `who-has`, `remember`, `recall`), plus `init-schema --repair-vector-indexes` for a
  vector index built with the wrong operator class (`src/roshambo/cli.py`).
- Two Agent Skills in `SKILL.md` format: `roshambo-remember-and-recall` and
  `roshambo-claim-work` (`skills/`).
- Documentation: `README.md` / `README_de.md`, `docs/mcp-managed.md` (the separate
  CockroachDB Managed MCP Server path), `docs/skills.md`,
  `docs/feedback-to-cockroachlabs.md`.
- `LICENSE` (Apache License 2.0) and `NOTICE`.
- Test suites for the core data model, AWS integration, and the MCP server
  (`tests/`), split between tests that run without any live cluster or cloud
  credentials and tests marked `live` / `aws` that are skipped cleanly without them.
- CI (`.github/workflows/ci.yml`): lint (`ruff`) plus the non-`live`/non-`aws` test
  suite, on every push and pull request against `main`.
- Infrastructure scripts (`infra/deploy_lambda.py`, IAM policy JSON, a `ccloud`
  provisioning wrapper) and a demo web application (`demo/`, FastAPI + static
  frontend, with a `mock`-mode fallback when no cluster is configured) — see
  [`docs/EVIDENCE-aws.md`](docs/EVIDENCE-aws.md) for what has actually been run there.

### Known gaps as of this entry

- A hosted demo URL and the submission video are not yet produced.
- The Bedrock/Titan embedding path exists but has not been run with real AWS
  credentials in this environment, so no claim of *semantic* (meaning-based) recall is
  made anywhere in this repository — only that a reworded query finds a prior entry
  again, which has been verified.
