# Evidence — CockroachDB Agent Skills

What was used, what it found, and what we did about it. Applied on 2026-07-31 during the
two-machine field run (`poc-starmap-2026-07-30`).

## What was used

`cockroachlabs/cockroachdb-skills`, cloned to `C:\_Local_DEV\repos\cockroachdb-skills`.
Skill applied: **`cockroachdb-sql`**
(`skills/cockroachdb-query-and-schema-design/cockroachdb-sql/SKILL.md`), the skill for
schema design and CockroachDB-specific SQL patterns. Its own description names the target:
*"CockroachDB anti-patterns like missing primary keys, sequential ID hotspots, or incorrect
type usage."*

The skill prescribes a procedure. We followed it against the live cluster rather than
reading it:

| Step in the skill | What we ran |
|---|---|
| 3 — Context gathering, query existing schema | `SHOW TABLES`, `SHOW CREATE TABLE claims`, `SHOW CREATE TABLE audit_log` |
| 4 — Apply rules, validate against anti-patterns | `references/cockroachdb-rules/04-optimization.md` |
| 5 — Validate against DB (mandatory) | row counts and recent-write rate on `audit_log` |

## What the schema actually looks like

```sql
CREATE TABLE public.claims (
    swarm_id STRING NOT NULL,
    resource STRING NOT NULL,
    claim_id UUID NOT NULL DEFAULT gen_random_uuid(),
    agent_id STRING NOT NULL,
    ...
    CONSTRAINT claims_pkey PRIMARY KEY (swarm_id ASC, resource ASC),
    CONSTRAINT claims_agent_fk FOREIGN KEY (swarm_id, agent_id)
        REFERENCES public.agents(swarm_id, agent_key),
    INDEX claims_by_expiry (swarm_id ASC, expires_at ASC)
);

CREATE TABLE public.audit_log (
    swarm_id STRING NOT NULL,
    event_id UUID NOT NULL DEFAULT gen_random_uuid(),
    ...
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT audit_log_pkey PRIMARY KEY (swarm_id ASC, created_at ASC, event_id ASC)
);
```

## Finding 1 — `claims` passes

The skill's rules on schema design want an explicit primary key that carries meaning and no
sequential integer key. `claims_pkey (swarm_id, resource)` is exactly that: the key *is* the
thing being contended for, which is also why the single-winner guarantee is a primary-key
conflict rather than application logic. `claim_id` is a `UUID` with `gen_random_uuid()`,
not a sequence. `claims_by_expiry` supports the expiry sweep. No change needed.

## Finding 2 — `audit_log` has a time-ordered primary key

`audit_log_pkey (swarm_id, created_at, event_id)` puts a monotonically increasing timestamp
in the leading position after `swarm_id`. This is the pattern `04-optimization.md` warns
about under sequential hotspots: within one swarm, every insert lands at the end of the same
key range, so writes concentrate on one range and its leaseholder instead of spreading.

**Measured, not assumed** (2026-07-31, during the run): 3430 rows total, **455 of them
written in the preceding ten minutes** — roughly 0.75 writes per second sustained, from 24
agents across two machines. All of those inserts share one `swarm_id`, so they all target
the same range tail.

**Why we are not changing it now.** At this volume the hotspot costs nothing measurable, and
the field run's evidence is written into this table — altering its primary key mid-experiment
would invalidate the record we are submitting. The skill's own remedy for this case is a
hash-sharded index (*"Use hash-sharded indexes for sequential data"*), which for a fresh
deployment would look like adding `USING HASH` to the time-ordered key. That is the right
change for a deployment that expects sustained high-frequency auditing across many agents.

**Recorded as a known limitation** rather than silently fixed or silently ignored.

## Finding 3 — no schema change was suggested for the hot path

Notably, the skill found nothing to correct in the path that actually decides contention
(`claims`). The single-winner property rests on the primary key of that table, and the rules
endorse how it is built. That is a useful negative result: the mechanism this project claims
is sound is sound by CockroachDB's own published guidance, not just by our tests.

## Scope of this evidence

This documents one skill applied to one schema on one day. It does not claim the whole skills
collection was exercised, and it does not claim the hotspot was fixed — it was found,
measured, and left in place with a reason.
