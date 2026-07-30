# Build contract — Roshambo

> **Purpose:** three agents build this repository in parallel. This file is the interface
> contract between them. It fixes ownership of files and the signatures each side may rely on,
> so nobody has to wait and nobody overwrites anybody.
> **Authoritative plan:** `MANIFEST.md` in the planning folder (see `docs/PLAN-POINTER.md`).
> Written 2026-07-25 by the orchestrator.

## Ownership — do not edit files owned by another lane

| Lane | Owner | Files it owns exclusively |
|---|---|---|
| **Core** | Opus | `schema/**`, `src/roshambo/__init__.py`, `src/roshambo/config.py`, `src/roshambo/db.py`, `src/roshambo/models.py`, `src/roshambo/memory.py`, `src/roshambo/leases.py`, `src/roshambo/errors.py`, `tests/test_core_*.py`, `tests/conftest.py` |
| **AWS** | Sonnet A | `src/roshambo/aws/**`, `src/roshambo/embeddings.py`, `infra/**`, `demo/**`, `tests/test_aws_*.py`, `.github/workflows/**` |
| **Interface** | Sonnet B | `src/roshambo/mcp/**`, `skills/**`, `docs/**`, `README.md`, `README_de.md`, `LICENSE`, `NOTICE`, `CHANGELOG.md`, `tests/test_mcp_*.py` |
| Shared, orchestrator only | — | `pyproject.toml`, `CONTRACT.md`, `.gitignore` |

If you need a change in someone else's file, write the request into `docs/HANDOFF.md` under a
dated heading instead of editing it.

## Frozen interfaces (may be relied upon before they exist)

### `roshambo.config`

```python
@dataclass(frozen=True)
class RoshamboConfig:
    dsn: str                  # postgresql://... (CockroachDB)
    swarm_id: str             # tenant/prefix key, used as vector-index prefix column
    embedding_dim: int = 1024
    lease_ttl_seconds: int = 300
    embedding_provider: str = "bedrock"   # "bedrock" | "local"
    aws_region: str = "eu-central-1"      # CockroachDB/Lambda/S3 region (latency-critical)
    bedrock_region: str = "us-east-2"     # separate: Bedrock quota, not latency-critical
    bedrock_model_id: str = "amazon.titan-embed-text-v2:0"
    s3_bucket: str | None = None

def load_config(env: Mapping[str, str] | None = None) -> RoshamboConfig: ...
```

Environment prefix is `ROSHAMBO_` (e.g. `ROSHAMBO_DSN`, `ROSHAMBO_SWARM_ID`, `ROSHAMBO_LEASE_TTL_SECONDS`).

### `roshambo.embeddings` — owned by AWS lane, called by Core

```python
class Embedder(Protocol):
    dim: int
    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]: ...

def get_embedder(cfg: RoshamboConfig) -> Embedder: ...
```

Two implementations: `BedrockEmbedder` (Amazon Titan Text Embeddings V2, 1024 dims) and
`DeterministicEmbedder` (hash-based, offline, **clearly marked as a placeholder** — used only so
tests can run without AWS credentials; never presented as a real semantic model).

`embed_batch` exists for callers' convenience only. **Rows are always INSERTed one at a time** —
CockroachDB documents that batch inserts degrade vector index quality.

### `roshambo.memory` — owned by Core, called by MCP lane and AWS lane

```python
class Roshambo:
    def __init__(self, cfg: RoshamboConfig, embedder: Embedder | None = None) -> None: ...

    # --- coordination ---
    def register_agent(self, framework: str, host: str,
                       capabilities: dict | None = None,
                       agent_id: str | None = None) -> str: ...
    def claim(self, resource: str, agent_id: str, intent: str,
              ttl_seconds: int | None = None) -> Claim | ClaimDenied: ...
    def heartbeat(self, claim_id: str) -> bool: ...
    def release(self, claim_id: str) -> bool: ...
    def who_has(self, resource: str) -> Claim | None: ...

    # --- memory ---
    def remember(self, topic: str, approach: str, outcome: Outcome, evidence: str,
                 agent_id: str | None = None, detail: dict | None = None,
                 artifact_uri: str | None = None) -> Trail: ...
    def recall(self, query: str, limit: int = 5,
               outcomes: Sequence[Outcome] | None = None) -> list[RecallHit]: ...
    def learn(self, statement: str, kind: FactKind = "lesson",
              confidence: Confidence = "medium", source_trail: str | None = None) -> Fact: ...

    # --- decisions ---
    def decide(self, question: str, choice: str, rationale: str,
               confidence: Confidence, provenance: Provenance,
               agent_id: str | None = None) -> Decision: ...

    def status(self) -> SwarmStatus: ...
```

Types (in `roshambo.models`, plain dataclasses):

```python
Outcome    = Literal["success", "failure", "abandoned", "inconclusive"]
FactKind   = Literal["fact", "lesson", "constraint"]
Confidence = Literal["high", "medium", "low"]
Provenance = Literal["agent-inferred", "human-confirmed", "human-corrected"]

@dataclass(frozen=True)
class Claim:       claim_id: str; resource: str; agent_id: str; intent: str; expires_at: datetime; framework: str; host: str
@dataclass(frozen=True)
class ClaimDenied: resource: str; held_by: str; intent: str; expires_at: datetime; framework: str; host: str
@dataclass(frozen=True)
class Trail:       trail_id: str; topic: str; approach: str; outcome: Outcome; evidence: str; created_at: datetime
@dataclass(frozen=True)
class RecallHit:   trail: Trail; distance: float; strength: float
@dataclass(frozen=True)
class Fact:        fact_id: str; kind: FactKind; statement: str; confidence: Confidence
@dataclass(frozen=True)
class Decision:    decision_id: str; question: str; choice: str; provenance: Provenance
@dataclass(frozen=True)
class SwarmStatus: agents: int; active_claims: int; trails: int; failures: int; facts: int
```

`claim()` returns `ClaimDenied` (not an exception) when another agent holds a valid lease —
the denial carries **who** holds it and **what** they are doing. That is a product feature,
not an error path.

Agent identity is registry-backed. `agents.agent_id` remains the internal UUID row key;
the caller-visible stable id is `agents.agent_key`, unique within a swarm.
`claims.agent_id` and non-null `audit_log.agent_id` reference `(swarm_id, agent_key)`.
Claims and audit events copy `framework` and `host` into snapshot columns when written,
so later registry updates cannot rewrite historical evidence. An unregistered legacy
caller is represented honestly as `framework=unregistered, host=unknown`; new field
runs register first.

### `roshambo.aws` — owned by AWS lane

```python
def put_artifact(cfg: RoshamboConfig, key: str, data: bytes, content_type: str) -> str  # returns s3:// URI
def lambda_handler(event: dict, context) -> dict                                     # roshambo-worker entry point
```

## Ground rules for every lane

1. **Public repository.** No absolute Windows paths, no personal names, no internal project names,
   no credentials, no German-only identifiers in code. Repository language is English.
2. **No overclaiming.** Only numbers we actually measured go into docs. Untested scale is described
   as untested, not implied.
3. **Vector inserts one row at a time.** Documented CockroachDB constraint.
4. **`swarm_id` is the vector index prefix column** — every vector query filters on it.
5. **Tests must run without cloud credentials** (skip-marked where a live cluster is required),
   but the acceptance criteria in the plan are only met by runs against a **real** CockroachDB.
6. Write progress notes to `docs/HANDOFF.md`, not into other lanes' files.
7. Do not `git commit` — the orchestrator commits, to keep lanes untangled. Leave the tree clean
   and report what you changed.
