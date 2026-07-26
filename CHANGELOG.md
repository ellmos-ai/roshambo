# Changelog

All notable changes to this project are documented in this file. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project has not yet cut a
tagged release, so everything below is under `[Unreleased]`.

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
