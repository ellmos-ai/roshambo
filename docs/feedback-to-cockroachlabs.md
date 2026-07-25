# Feedback to Cockroach Labs

The hackathon submission form lists this as optional: "provide feedback on the
CockroachDB AI tools or features." This document is that feedback, written from the
concrete friction points this project actually hit while building on CockroachDB's
vector search — not a generic wishlist. Each point links to the line in this repository
where the issue is handled or documented, so the claim is checkable against real code
rather than taken on faith.

## 1. A wrong vector index operator class fails silently, not loudly

`CREATE VECTOR INDEX (swarm_id, embedding)` without an explicit operator class defaults
to `vector_l2_ops`. A query using the cosine operator `<=>` against an index built with
`vector_l2_ops` does not error — CockroachDB just does not use the index for that query,
and the query still returns correct results via a full scan. The mistake is invisible
until someone runs `EXPLAIN` and notices the scan, which is a poor failure mode for
something that changes query cost by orders of magnitude at scale rather than changing
correctness at all.

**What would have helped:** a planner warning (not an error — the query is still
correct) when a `VECTOR INDEX` exists on a column but its operator class does not match
the distance operator used in a query against that column. This is exactly the kind of
mismatch a human reviewing `EXPLAIN` output catches immediately and a first-time user
does not.

See `schema/001_init.sql`, design note 3, and the same file's explicit
`vector_cosine_ops` on both `trails_by_swarm` and `facts_by_swarm` — added specifically
because the default silently produced a working-but-unindexed query during development.
Verified against CockroachDB v25.4.0; see `docs/EVIDENCE-core.md` for the run.

## 2. Vector index availability differed across the CockroachDB releases this project tested against

`SET CLUSTER SETTING feature.vector_index.enabled = true` was required on the CockroachDB
release where vector indexes were a gated preview feature (v25.2-era); on later releases
the setting no longer exists and the statement itself errors. `schema/001_init.sql`'s
applier (`src/roshambo/db.py`, `_is_tolerable_setting_error`) has to special-case exactly
this one statement so the same schema file works unmodified whether or not the gate still
exists on the target cluster.

**What would have helped:** either keeping the setting as a permanently-accepted no-op
after the feature graduated from preview (so older schema scripts stay valid without a
special case), or a single documented "vector indexes are GA as of vX.Y, no setting
required" note that this project could have linked instead of writing tolerance code for
a moving target.

## 3. The batch-insert-degrades-vector-index-quality constraint is easy to miss until it is load-bearing

CockroachDB's own documentation states that batch-inserting rows into a table with a
vector index degrades index quality, so `remember()` and `learn()` in
`src/roshambo/memory.py` insert exactly one row per call, by design — see `CONTRACT.md`,
ground rule 3. This is a real and reasonable constraint given how the index is built
incrementally, but it is the kind of thing a team discovers by reading deep
documentation, not something the `INSERT` statement itself warns about if someone (later,
under load, trying to speed up ingestion) reaches for a multi-row `INSERT ... VALUES
(...), (...), (...)` against a vector-indexed table.

**What would have helped:** a lint-level warning from `EXPLAIN` or the row-count
threshold at which a multi-row insert into a vector-indexed table is flagged, so the
tradeoff is visible at the point where someone is about to make it, not only in prose
documentation they may not have read yet.

## 4. `IMPORT INTO` on a table with a vector index

`IMPORT INTO` is not supported on tables carrying a vector index (see the top-level
`README.md`, "Known limitations"). For a project seeding a moderate amount of demo or
test data, this pushes every insert through the single-row path noted above, which is
consistent with point 3 but worth naming as its own limitation: `IMPORT INTO` is the tool
most people reach for to seed a table quickly, and its silent absence for vector-indexed
tables was learned by hitting it, not by a warning up front.

## What worked without friction, for balance

This document exists to record friction, but two things deserve credit precisely because
they were not friction: the `INSERT ... ON CONFLICT ... DO UPDATE ... WHERE
claims.expires_at < now()` pattern for `claims` (`schema/001_init.sql`, design note 2)
gave exclusive, race-free lease acquisition in one atomic statement with no read-then-write
window and no application-level locking — exactly the primitive this project needed and
exactly as documented. And `SQLSTATE 40001` on serialization failure (`src/roshambo/db.py`,
`retry_on_serialization_failure`) is a small, predictable, well-documented surface to
retry against; nothing about it needed guessing.
