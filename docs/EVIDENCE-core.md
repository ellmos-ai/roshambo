# Evidence — core lane

Everything below was executed, not estimated. Numbers that are not in a pasted command
output are not claimed anywhere in this repository.

## Environment

| | |
|---|---|
| CockroachDB | `CockroachDB CCL v25.4.0 (x86_64-w64-mingw32, built 2025/10/28 18:45:20, go1.23.12)` |
| Topology | single node, `start-single-node --insecure`, local disk |
| Python | 3.12.10 |
| Driver | psycopg 3.3.4 (`psycopg-binary`) |
| pytest | 9.1.1 with pytest-timeout 2.4.0 |
| Embedder | `PlaceholderEmbedder` (hash-based, offline). No AWS credentials were present. |
| Date of run | 2026-07-25, 07:01–07:03 local |

> **Every result below is from a repeated run.** During the first pass, an unrelated
> process on the same machine issued `taskkill /F /IM python.exe` twice, which terminates
> *all* Python processes on the host — including, potentially, a running `pytest` or the
> worker processes of the multi-process test. A truncated run that happens to show "one
> winner" proves nothing, so the concurrency measurement and the full suite were both
> re-run afterwards from a clean process table and the earlier output was discarded. The
> numbers below are the repeat. The CockroachDB node (`cockroach.exe`) was never affected
> and ran continuously from 06:36.
>
> For the record, a kill of that kind cannot forge a passing result: a terminated `pytest`
> prints no summary line at all, and a terminated pool worker surfaces as
> `BrokenProcessPool`, which fails the test. The re-run was done because "it probably
> could not have faked it" is not evidence.

Vector indexes require v25.2 or newer. On v25.4.0 they are generally available, so the
`SET CLUSTER SETTING feature.vector_index.enabled = true` line in `schema/001_init.sql`
applies without error; the applier tolerates its absence on other releases.

## Commands

Start the node (the binary and its store live outside the repository — neither is
committed, and neither belongs in a synced cloud folder):

```
cockroach start-single-node --insecure \
  --store=<local-path>/_data \
  --listen-addr=localhost:26257 \
  --http-addr=localhost:8181
```

`--background` is not available in the Windows build; the process was started detached
instead.

Create the database and run the suite:

```
cockroach sql --insecure --host=127.0.0.1:26257 --execute="CREATE DATABASE IF NOT EXISTS cairn;"

export PYTHONIOENCODING=utf-8
export CAIRN_DSN="postgresql://root@127.0.0.1:26257/cairn?sslmode=disable"
python -m pytest tests/test_core_config.py tests/test_core_embedder.py \
                 tests/test_core_schema.py tests/test_core_leases.py \
                 tests/test_core_concurrency.py tests/test_core_recall.py -v
```

### Use `127.0.0.1`, not `localhost`, in the DSN

Measured, because it is not obvious and it costs minutes:

```
OK   postgresql://root@127.0.0.1:26257/cairn?sslmode=disable   0.02 s
OK   postgresql://root@localhost:26257/cairn?sslmode=disable   8.09 s
```

`localhost` resolves to `::1` first on this host while the node listens on `127.0.0.1`
only, so every connection paid an 8-second failed IPv6 attempt before falling back. Both
work; one is 400× slower per connection. This is a property of the environment, not of
Cairn — no code changed because of it.

## Acceptance criterion 1 — twenty concurrent claims, exactly one winner

**Met.**

Twenty OS threads, each with its own `Cairn` instance and its own psycopg connection to
the real node, released together from a `threading.Barrier`. Connections are opened
*before* the barrier so that TCP setup does not stagger the contenders.

Because "exactly one winner" is also what a strictly serial run produces, the overlap was
measured rather than assumed. `test_the_claims_really_did_overlap_in_time` timestamps each
claim statement and computes the peak number in flight at any instant:

| Run | Time | Contenders | Peak concurrent claims | Winners |
|---|---|---|---|---|
| 1 | 07:01:41 | 20 | **20** | 1 |
| 2 | 07:01:44 | 20 | **20** | 1 |
| 3 | 07:01:48 | 20 | **20** | 1 |
| 4 | 07:01:56 | 20 | **20** | 1 |
| 5 | 07:02:01 | 20 | **20** | 1 |

All twenty statements were in flight simultaneously in all five runs, so the single-winner
result was decided under real contention. These five runs are the repeat described at the
top of this document; an earlier set of three showed the same 20/20 but overlapped the
window in which the stray `taskkill` occurred, so it was discarded rather than reported.

The same criterion also holds across twenty **separate OS processes**
(`test_twenty_separate_processes_also_produce_exactly_one_winner`), which removes the
"it is only threads sharing one interpreter" objection.

Covered cases:

- 20 concurrent claims on one resource → exactly 1 `Claim`, 19 `ClaimDenied`.
- All 19 denials name the same holder, and that holder is the one in the database.
- Expired-lease takeover under contention: a lease is seeded with a 1 s TTL, allowed to
  lapse, then 20 threads race to take it over → exactly 1 winner, with a **new**
  `claim_id`. This is the riskier path, because the `ON CONFLICT` branch fires in all 20
  transactions at once and each sees an expired row.
- 20 concurrent claims on 20 *distinct* resources → all 20 granted, i.e. the lease does
  not serialise unrelated work.

The property is enforced by the database, not by application logic: `claims` is keyed on
`(swarm_id, resource)`, and acquisition is a single
`INSERT ... ON CONFLICT ... DO UPDATE ... WHERE claims.expires_at < now()`. There is no
read-then-write window in `src/cairn/leases.py` to race against.

## Acceptance criterion 2 — a stored failure is found again through different wording

**Met for lexical retrieval. Not met — and not claimed — for semantic retrieval.**

The run used `PlaceholderEmbedder`, which hashes word tokens and character trigrams. It
is not a semantic model and is never described as one. The test that carries the
criterion is named accordingly:

```
test_rephrased_query_finds_the_failure_at_rank_one_lexically_not_semantically
```

What was demonstrated: five trails are stored — one failure plus four distractors written
to the same shape and length, so nothing wins on brevity. A query that shares **no
three-word phrase** with the stored failure (guarded by
`test_no_long_phrase_is_shared_between_query_and_target`) retrieves that failure at rank 1
through the real write path, the `VECTOR(1024)` column, the cosine vector index and the
ranking in `recall()`.

The mechanism was then pinned down rather than left to the reader's imagination. The
query and the winning trail share exactly two whole words — "a" and "on" — and two
distractors share two words as well, so whole-word overlap does **not** separate them.
What does is the character-trigram half of the feature space: count/counter/counters,
request/requests, server/servers, limit/limiting. Two tests assert exactly this
(`test_the_rank_one_hit_is_explained_by_character_trigrams_not_by_words`,
`test_shared_words_between_query_and_winner_are_only_stopwords`), so the write-up cannot
quietly drift into calling a sub-word string effect "semantic".

**A real semantic result requires Amazon Titan Text Embeddings V2 via Bedrock.** That test
exists (`test_recall_with_the_real_embedder`, marked `aws`) and **skipped** in this run
because no credentials were present. Until it has run, this repository claims lexical
retrieval and nothing more.

## Deviation from the schema, and why

One deviation was found, and it was found by a test rather than by reading the file.

`test_recall_actually_uses_the_vector_index` failed on the first live run. `schema/001_init.sql`
declares `vector_cosine_ops`, but `SHOW CREATE TABLE trails` on the running cluster
reported:

```
VECTOR INDEX trails_by_swarm (swarm_id, embedding vector_l2_ops)
```

The table had been created by an earlier revision of the schema file, and
`CREATE TABLE IF NOT EXISTS` is a no-op against a table that already exists — so the
corrected op class never reached the cluster and nothing reported a problem.

The consequence was measured directly, same table, same 200 rows in one swarm, operator
alone changed:

```
-- <-> (L2 operator) against the vector_l2_ops index
└── • lookup join
    └── • vector search
          table: trails@trails_by_swarm

-- <=> (cosine operator, what recall() uses) against the same index
└── • render
    └── • scan
          table: trails@trails_pkey
          spans: [/'probe-1784953401' - /'probe-1784953401']
```

The cosine query still returned correct rows. It simply stopped using the index. That is
the whole danger of this mistake: nothing fails, nothing is logged, and it is invisible
until someone runs `EXPLAIN`.

After dropping and recreating both vector indexes with `vector_cosine_ops`, `<=>` plans a
`vector search` and the guard test passes.

### What changed in the code as a result

Repairing the local cluster fixes one machine; the trap stays. Three changes address it:

1. `src/cairn/db.py` gained `find_vector_index_mismatches()`, which reads the op class
   back out of `SHOW CREATE TABLE` (neither `information_schema` nor `SHOW INDEXES`
   reports it on v25.4.0) and compares it against `VECTOR_INDEXES`.
2. `apply_schema()` now runs that check after applying the file and **raises
   `SchemaError`** on a mismatch, quoting the exact repair statements. A silent
   performance cliff became a loud error.
3. Repair is opt-in — `apply_schema(..., repair_vector_indexes=True)` or
   `cairn init-schema --repair-vector-indexes` — because rebuilding a vector index on a
   populated cluster is real work and should not happen as a side effect of "apply the
   schema". The test suite enables it, since a test cluster is exactly where stale tables
   accumulate.

`tests/test_core_schema.py` builds the broken state on purpose against the live cluster
(a scratch table with a deliberately wrong `vector_l2_ops` index) and checks that it is
detected, that a missing index is not mistaken for a matching one, that `apply_schema`
refuses to finish quietly, and that the identifier path used by `DROP INDEX` — which
cannot bind parameters — rejects anything that is not a bare identifier.

No other deviation from `schema/001_init.sql` was found: after the fixture applies the
schema, `find_vector_index_mismatches` over the production indexes returns empty
(`test_apply_schema_leaves_the_real_indexes_matching`).

## Full test output

```
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0
rootdir: <repo>
configfile: pyproject.toml
plugins: anyio-4.14.2, timeout-2.4.0
timeout: 120.0s
timeout method: thread
timeout func_only: False
collecting ... collected 65 items

tests/test_core_config.py::test_load_config_reads_the_cairn_prefix PASSED [  1%]
tests/test_core_config.py::test_load_config_defaults PASSED              [  3%]
tests/test_core_config.py::test_load_config_without_dsn_is_an_error PASSED [  4%]
tests/test_core_config.py::test_load_config_rejects_non_integer_ttl PASSED [  6%]
tests/test_core_config.py::test_config_validates_its_fields[swarm_id-] PASSED [  7%]
tests/test_core_config.py::test_config_validates_its_fields[embedding_dim-0] PASSED [  9%]
tests/test_core_config.py::test_config_validates_its_fields[lease_ttl_seconds--1] PASSED [ 10%]
tests/test_core_config.py::test_config_is_frozen PASSED                  [ 12%]
tests/test_core_config.py::test_vector_literal_round_trips_shape PASSED  [ 13%]
tests/test_core_config.py::test_statement_splitter_ignores_semicolons_in_comments PASSED [ 15%]
tests/test_core_config.py::test_statement_splitter_on_the_real_schema PASSED [ 16%]
tests/test_core_embedder.py::test_dimension_matches_the_schema_default PASSED [ 18%]
tests/test_core_embedder.py::test_vectors_are_unit_length PASSED         [ 20%]
tests/test_core_embedder.py::test_it_is_deterministic PASSED             [ 21%]
tests/test_core_embedder.py::test_empty_text_still_yields_a_usable_vector PASSED [ 23%]
tests/test_core_embedder.py::test_shared_vocabulary_is_closer_than_unrelated_text PASSED [ 24%]
tests/test_core_embedder.py::test_batch_matches_single PASSED            [ 26%]
tests/test_core_embedder.py::test_zero_dimension_is_rejected PASSED      [ 27%]
tests/test_core_embedder.py::test_it_announces_itself_as_a_placeholder PASSED [ 29%]
tests/test_core_embedder.py::test_placeholder_provider_selects_it_without_touching_the_cloud PASSED [ 30%]
tests/test_core_embedder.py::test_an_explicit_embedder_always_wins PASSED [ 32%]
tests/test_core_schema.py::test_a_wrong_op_class_is_reported PASSED      [ 33%]
tests/test_core_schema.py::test_repair_replaces_the_index_with_the_right_op_class PASSED [ 35%]
tests/test_core_schema.py::test_a_missing_index_is_reported_as_missing_not_as_matching PASSED [ 36%]
tests/test_core_schema.py::test_apply_schema_leaves_the_real_indexes_matching PASSED [ 38%]
tests/test_core_schema.py::test_apply_schema_refuses_to_finish_quietly_on_a_mismatch PASSED [ 40%]
tests/test_core_schema.py::test_apply_schema_repairs_when_asked PASSED   [ 41%]
tests/test_core_schema.py::test_index_names_are_constrained_to_plain_identifiers PASSED [ 43%]
tests/test_core_leases.py::test_first_claim_is_granted PASSED            [ 44%]
tests/test_core_leases.py::test_second_claim_is_denied_and_says_who_holds_it PASSED [ 46%]
tests/test_core_leases.py::test_different_resources_do_not_collide PASSED [ 47%]
tests/test_core_leases.py::test_who_has_reports_the_holder_and_the_intent PASSED [ 49%]
tests/test_core_leases.py::test_a_foreign_agent_cannot_release_a_lease_it_does_not_hold PASSED [ 50%]
tests/test_core_leases.py::test_who_has_does_not_leak_the_claim_id PASSED [ 52%]
tests/test_core_leases.py::test_release_frees_the_resource PASSED        [ 53%]
tests/test_core_leases.py::test_release_of_an_unknown_claim_is_false_not_an_error PASSED [ 55%]
tests/test_core_leases.py::test_expired_lease_is_taken_over PASSED       [ 56%]
tests/test_core_leases.py::test_heartbeat_extends_a_live_lease PASSED    [ 58%]
tests/test_core_leases.py::test_heartbeat_cannot_resurrect_an_expired_lease PASSED [ 60%]
tests/test_core_leases.py::test_heartbeat_of_a_taken_over_lease_is_false PASSED [ 61%]
tests/test_core_leases.py::test_release_after_takeover_does_not_steal_the_new_lease PASSED [ 63%]
tests/test_core_leases.py::test_reclaiming_your_own_live_lease_is_denied_not_extended PASSED [ 64%]
tests/test_core_concurrency.py::test_twenty_concurrent_claims_produce_exactly_one_winner PASSED [ 66%]
tests/test_core_concurrency.py::test_the_claims_really_did_overlap_in_time PASSED [ 67%]
tests/test_core_concurrency.py::test_every_loser_is_told_who_won PASSED  [ 69%]
tests/test_core_concurrency.py::test_the_winner_is_the_one_recorded_in_the_database PASSED [ 70%]
tests/test_core_concurrency.py::test_concurrent_claims_on_distinct_resources_all_succeed PASSED [ 72%]
tests/test_core_concurrency.py::test_twenty_separate_processes_also_produce_exactly_one_winner PASSED [ 73%]
tests/test_core_concurrency.py::test_takeover_of_an_expired_lease_is_also_single_winner PASSED [ 75%]
tests/test_core_recall.py::test_no_long_phrase_is_shared_between_query_and_target PASSED [ 76%]
tests/test_core_recall.py::test_rephrased_query_finds_the_failure_at_rank_one_lexically_not_semantically PASSED [ 78%]
tests/test_core_recall.py::test_the_rank_one_hit_is_explained_by_character_trigrams_not_by_words PASSED [ 80%]
tests/test_core_recall.py::test_shared_words_between_query_and_winner_are_only_stopwords PASSED [ 81%]
tests/test_core_recall.py::test_recall_actually_uses_the_vector_index PASSED [ 83%]
tests/test_core_recall.py::test_recall_returns_hits_ordered_by_distance PASSED [ 84%]
tests/test_core_recall.py::test_recall_respects_the_limit PASSED         [ 86%]
tests/test_core_recall.py::test_recall_can_be_restricted_to_failures PASSED [ 87%]
tests/test_core_recall.py::test_recall_on_an_empty_swarm_returns_nothing PASSED [ 89%]
tests/test_core_recall.py::test_recall_does_not_cross_swarm_boundaries PASSED [ 90%]
tests/test_core_recall.py::test_remembered_trail_round_trips PASSED      [ 92%]
tests/test_core_recall.py::test_reinforcement_raises_strength PASSED     [ 93%]
tests/test_core_recall.py::test_reinforcing_an_unknown_trail_returns_none PASSED [ 95%]
tests/test_core_recall.py::test_invalid_outcome_is_rejected_before_touching_the_database PASSED [ 96%]
tests/test_core_recall.py::test_learn_and_decide_and_status PASSED       [ 98%]
tests/test_core_recall.py::test_recall_with_the_real_embedder SKIPPED    [100%]

======================= 64 passed, 1 skipped in 28.59s ========================
```

Run started 07:02:12 and finished 07:02:49, i.e. entirely after the stray `taskkill`
described at the top of this document, with a process table containing no Python
processes of this lane.

The single skip is `test_recall_with_the_real_embedder`, which needs AWS credentials and
Bedrock model access. It skips loudly, by design: the placeholder result must never be
mistaken for a semantic measurement.

### Without a cluster

`CONTRACT.md` requires the suite to be green on a machine with no database. With
`CAIRN_DSN` unset:

```
.....................ssssssssssssssssssssssssssss.ss.ssssssssssss        [100%]
23 passed, 42 skipped in 1.27s
```

## CLI end to end

Against the same node, with the placeholder embedder:

```
$ cairn init-schema
ok           CREATE TABLE IF NOT EXISTS decisions (
ok           CREATE TABLE IF NOT EXISTS audit_log (
ok           CREATE INDEX IF NOT EXISTS claims_by_expiry ON claims (swarm_id, expires_at)
8 statement(s) applied

$ cairn claim "repo:demo:parser.py" --agent-id agent-a --intent "rewrite the tokenizer"
granted 36f806f9-61e1-46f4-9f5a-9263c8b7e042 until 2026-07-25T05:00:14.996929+00:00

$ cairn claim "repo:demo:parser.py" --agent-id agent-b --intent "add error recovery"
denied: held by agent-a until 2026-07-25T05:00:14.996929+00:00 — rewrite the tokenizer
exit=3

$ cairn who-has "repo:demo:parser.py"
agent-a until 2026-07-25T05:00:14.996929+00:00 — rewrite the tokenizer

$ cairn recall "counting requests locally on each node to cap client calls" --limit 2
1. [failure] rate limiting (distance 0.8697, strength 1.0)
   approach: per-process counter in memory
   evidence: counters drifted apart across servers, throttled at four times the limit

$ cairn status
swarm=<generated> agents=0 active_claims=1 trails=1 failures=1 facts=0
```

The denial exits 3 rather than 0 or 1, so a shell script can tell "somebody else has it"
apart from "the command failed".

## What is not covered

- **No semantic measurement.** See criterion 2 above.
- **Single node.** Every concurrency result here is from one `start-single-node` process.
  Nothing has been measured on a multi-node cluster, and no claim about cross-region
  behaviour is made anywhere in this repository.
- **No scale numbers.** The largest table in these runs held a few hundred rows. Vector
  recall latency and index quality at realistic volumes are unmeasured, so they are
  unstated.
- **Windows only.** The suite has not been run on Linux or macOS.
