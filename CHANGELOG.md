# Changelog

All notable changes to this project are documented in this file. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project has not yet cut a
tagged release, so everything below is under `[Unreleased]`.

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
