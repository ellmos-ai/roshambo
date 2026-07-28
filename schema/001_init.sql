-- Roshambo schema — CockroachDB
--
-- Idempotent. Safe to run repeatedly against the same cluster.
--
-- Design notes that are load-bearing (do not "simplify" these away):
--
--  1. `swarm_id` is the leading column of every primary key AND the prefix column of
--     every VECTOR INDEX. CockroachDB only accelerates a filtered vector search when
--     the filter is on the index prefix columns, so every recall query must carry a
--     `WHERE swarm_id = $1` predicate.
--
--  2. `claims` is keyed on (swarm_id, resource) — exclusivity is enforced by the
--     primary key, not by application logic. Acquisition is a single
--     INSERT ... ON CONFLICT ... DO UPDATE ... WHERE claims.expires_at < now(),
--     which is one atomic statement. There is no read-then-write window.
--
--  3. The vector indexes declare `vector_cosine_ops` explicitly. A bare
--     `VECTOR INDEX (swarm_id, embedding)` defaults to `vector_l2_ops`, and CockroachDB
--     will then refuse to use it for the cosine operator `<=>` — the query still returns
--     correct rows, it just full-scans, so the mistake is invisible until you EXPLAIN.
--     The operator and the op class must agree: <-> needs vector_l2_ops, <=> needs
--     vector_cosine_ops. `recall()` uses <=>. Measured on v25.4.0, same table and same
--     200 rows: <-> planned a `vector search`, <=> planned a full `scan`. See
--     docs/EVIDENCE-core.md.
--
--     `CREATE TABLE IF NOT EXISTS` cannot repair this. On a cluster where the table
--     already exists the statement is a no-op, so an index built by an older revision
--     of this file keeps its old op class forever and nothing reports it. Applying the
--     schema therefore also verifies the op class (src/roshambo/db.py,
--     `find_vector_index_mismatches`) and refuses to finish quietly on a mismatch;
--     `roshambo init-schema --repair-vector-indexes` rebuilds the index.
--
--  4. Vector rows are INSERTed one at a time; batch inserts degrade index quality.
--     That is a property of the writer (see src/roshambo/memory.py), not of the schema,
--     but it is recorded here because the schema is what makes the index exist.

-- Vector indexes were a preview feature gated behind this cluster setting when they
-- were introduced (v25.2). On releases where the setting no longer exists the
-- statement errors; the applier in src/roshambo/db.py tolerates that and continues.
SET CLUSTER SETTING feature.vector_index.enabled = true;

-- Who is in the swarm?
CREATE TABLE IF NOT EXISTS agents (
    swarm_id       STRING NOT NULL,
    agent_id       UUID   NOT NULL DEFAULT gen_random_uuid(), -- internal registry row id
    agent_key      STRING NOT NULL DEFAULT gen_random_uuid()::STRING,
    framework      STRING NOT NULL,           -- claude | codex | gemini | kimi | lambda | ...
    host           STRING NOT NULL,
    capabilities   JSONB  NOT NULL DEFAULT '{}',
    registered_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_heartbeat TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (swarm_id, agent_id),
    UNIQUE INDEX agents_by_key (swarm_id, agent_key)
);

-- Distributed leases. One row per (swarm, resource); the primary key is the mutex.
CREATE TABLE IF NOT EXISTS claims (
    swarm_id     STRING NOT NULL,
    resource     STRING NOT NULL,             -- e.g. "repo:roshambo:src/memory.py"
    claim_id     UUID   NOT NULL DEFAULT gen_random_uuid(),
    agent_id     STRING NOT NULL,
    framework_snapshot STRING NOT NULL DEFAULT 'unknown',
    host_snapshot STRING NOT NULL DEFAULT 'unknown',
    intent       STRING NOT NULL,             -- human-readable: what the holder intends to do
    acquired_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (swarm_id, resource),
    CONSTRAINT claims_agent_fk FOREIGN KEY (swarm_id, agent_id)
      REFERENCES agents (swarm_id, agent_key)
);

-- Trails: attempts and how they ended — the negative memory.
CREATE TABLE IF NOT EXISTS trails (
    swarm_id     STRING NOT NULL,
    trail_id     UUID   NOT NULL DEFAULT gen_random_uuid(),
    agent_id     STRING,
    topic        STRING NOT NULL,
    approach     STRING NOT NULL,             -- what was tried
    outcome      STRING NOT NULL,             -- success | failure | abandoned | inconclusive
    evidence     STRING NOT NULL,             -- why it ended that way (error text, measurement, quote)
    detail       JSONB  NOT NULL DEFAULT '{}',
    artifact_uri STRING,                      -- s3://... for large attachments
    embedding    VECTOR(1024) NOT NULL,
    strength     FLOAT  NOT NULL DEFAULT 1.0, -- stigmergy: reinforced when re-confirmed
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (swarm_id, trail_id),
    VECTOR INDEX trails_by_swarm (swarm_id, embedding vector_cosine_ops)
);

-- Curated knowledge distilled from trails.
CREATE TABLE IF NOT EXISTS facts (
    swarm_id       STRING NOT NULL,
    fact_id        UUID   NOT NULL DEFAULT gen_random_uuid(),
    kind           STRING NOT NULL,           -- fact | lesson | constraint
    statement      STRING NOT NULL,
    source_trail   UUID,
    confidence     STRING NOT NULL,           -- high | medium | low
    embedding      VECTOR(1024) NOT NULL,
    valid_from     TIMESTAMPTZ NOT NULL DEFAULT now(),
    invalidated_at TIMESTAMPTZ,
    PRIMARY KEY (swarm_id, fact_id),
    VECTOR INDEX facts_by_swarm (swarm_id, embedding vector_cosine_ops)
);

-- Decision ledger with provenance.
CREATE TABLE IF NOT EXISTS decisions (
    swarm_id      STRING NOT NULL,
    decision_id   UUID   NOT NULL DEFAULT gen_random_uuid(),
    agent_id      STRING,
    question      STRING NOT NULL,
    choice        STRING NOT NULL,
    rationale     STRING NOT NULL,
    confidence    STRING NOT NULL,            -- high | medium | low
    provenance    STRING NOT NULL,            -- agent-inferred | human-confirmed | human-corrected
    superseded_by UUID,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (swarm_id, decision_id)
);

-- Black box: append-only, for post-mortems and for showing "safe by default".
CREATE TABLE IF NOT EXISTS audit_log (
    swarm_id   STRING NOT NULL,
    event_id   UUID   NOT NULL DEFAULT gen_random_uuid(),
    agent_id   STRING,
    framework_snapshot STRING,
    host_snapshot STRING,
    verb       STRING NOT NULL,               -- claim | release | remember | recall | decide | ...
    resource   STRING,
    allowed    BOOL   NOT NULL,
    reason     STRING,
    latency_ms INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (swarm_id, created_at, event_id),
    CONSTRAINT audit_agent_fk FOREIGN KEY (swarm_id, agent_id)
      REFERENCES agents (swarm_id, agent_key)
);

-- Secondary index used by status() and by expiry sweeps.
CREATE INDEX IF NOT EXISTS claims_by_expiry ON claims (swarm_id, expires_at);

-- Upgrade path for clusters created before registry-backed agent identities existed.
-- Additive and idempotent: historical free-form ids become explicit legacy identities,
-- while their original claim/audit rows remain intact.
ALTER TABLE agents ADD COLUMN IF NOT EXISTS agent_key STRING;
UPDATE agents
   SET agent_key = agent_id::STRING
 WHERE agent_key IS NULL;
ALTER TABLE agents ALTER COLUMN agent_key SET DEFAULT gen_random_uuid()::STRING;
ALTER TABLE agents ALTER COLUMN agent_key SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS agents_by_key ON agents (swarm_id, agent_key);

ALTER TABLE claims ADD COLUMN IF NOT EXISTS framework_snapshot STRING;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS host_snapshot STRING;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS framework_snapshot STRING;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS host_snapshot STRING;

INSERT INTO agents (swarm_id, agent_key, framework, host, capabilities)
SELECT DISTINCT swarm_id, agent_id, 'legacy', 'unknown', '{"migrated": true}'::JSONB
  FROM (
        SELECT swarm_id, agent_id FROM claims
        UNION
        SELECT swarm_id, agent_id FROM audit_log WHERE agent_id IS NOT NULL
       ) AS historical
 WHERE agent_id IS NOT NULL
ON CONFLICT (swarm_id, agent_key) DO NOTHING;

-- Backfill once, while the newly-added columns are NULL. On every later schema run
-- these rows are left alone, even if the current registry entry has changed.
UPDATE claims AS c
   SET framework_snapshot = a.framework,
       host_snapshot = a.host
  FROM agents AS a
 WHERE c.swarm_id = a.swarm_id
   AND c.agent_id = a.agent_key
   AND c.framework_snapshot IS NULL
   AND c.host_snapshot IS NULL;

UPDATE audit_log AS l
   SET framework_snapshot = a.framework,
       host_snapshot = a.host
  FROM agents AS a
 WHERE l.swarm_id = a.swarm_id
   AND l.agent_id = a.agent_key
   AND l.framework_snapshot IS NULL
   AND l.host_snapshot IS NULL;

ALTER TABLE claims ALTER COLUMN framework_snapshot SET DEFAULT 'unknown';
ALTER TABLE claims ALTER COLUMN host_snapshot SET DEFAULT 'unknown';
ALTER TABLE claims ALTER COLUMN framework_snapshot SET NOT NULL;
ALTER TABLE claims ALTER COLUMN host_snapshot SET NOT NULL;

ALTER TABLE claims ADD CONSTRAINT claims_agent_fk
    FOREIGN KEY (swarm_id, agent_id) REFERENCES agents (swarm_id, agent_key);
ALTER TABLE audit_log ADD CONSTRAINT audit_agent_fk
    FOREIGN KEY (swarm_id, agent_id) REFERENCES agents (swarm_id, agent_key);
