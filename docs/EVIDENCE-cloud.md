# Evidence — cloud acceptance

Acceptance run of the full live suite against a real CockroachDB **Cloud** cluster, after the
local-node runs recorded in `EVIDENCE-core.md`. Everything below was executed, not estimated.

> Naming note: the project was renamed from its working name `cairn` to `roshambo` on
> 2026-07-25. Commands in the older evidence documents are recorded verbatim under the old
> name; commands below use the new one. Same code, one rename pass in between
> (49 files, zero leftovers outside the historical evidence documents).

## Environment

| | |
|---|---|
| Cluster | CockroachDB Cloud **Basic** (serverless), provider AWS, region `eu-central-1` (Frankfurt) |
| Server version | `CockroachDB CCL v26.2.1 (x86_64-pc-linux-gnu, built 2026/05/21)` |
| Connection | PostgreSQL wire, `sslmode=verify-full`, CA cert from the cluster console |
| Client | Python 3.12.10, psycopg 3.3.4, pytest 9.1.1 |
| Embedder | offline placeholder (no AWS credentials present); the one Bedrock-marked test skips |
| Date of run | 2026-07-25, ~07:50–08:20 local |

## Result

```
python -m pytest tests/ -m live --timeout=600
=========== 45 passed, 1 skipped, 72 deselected in 91.27s (0:01:31) ===========
```

(The re-run after the rename pass produced the same result: 45 passed, 1 skipped, 97.53s.
The skip is `test_recall_with_the_real_embedder`, which requires Amazon Bedrock credentials.)

This includes the concurrency acceptance — twenty genuinely concurrent claims, exactly one
winner, denial names the holder — now against a managed, distributed cloud cluster rather
than a local single node, over TLS and ~15 ms WAN latency per round trip.

## Findings unique to the cloud run

Both were found by tests, both led to code or test changes, and both are the kind of thing
worth reporting back to the vendor (see `feedback-to-cockroachlabs.md`).

### 1. v26.2 plans vector queries cost-based; v25.4 did not

On the local v25.4 node, any `<=>` query against a correctly-typed vector index planned a
`vector search`. On cloud v26.2, the same query against a **small** swarm (5 rows) plans a
full scan — correctly: for five rows the scan is cheaper.

Consequence: the guard test `test_recall_actually_uses_the_vector_index` (which exists to
catch a silent op-class mismatch between the index and `recall()`'s distance operator) could
no longer assert "the planner picked the index", because whether it picks it is a cost
decision that depends on table size and statistics freshness.

The guard now forces the decision instead of guessing costs, using a table hint — and
asserts **both directions**, measured against the cloud cluster:

```
EXPLAIN SELECT ... FROM trails@trails_by_swarm ... ORDER BY embedding <=> ...  -- cosine
  → plans "vector search"                                  (index serves recall()'s operator)

EXPLAIN SELECT ... FROM trails@trails_by_swarm ... ORDER BY embedding <-> ...  -- L2
  → ERROR: index "trails_by_swarm" cannot be used for this query
                                        (mismatched operator is refused loudly, not silently)
```

That turns the silent performance cliff documented in `EVIDENCE-core.md` into a
deterministic, planner-independent test.

### 2. `ANALYZE` inside an open transaction has no planning effect

A statistics refresh issued on a connection with an open transaction did not influence
planning; the same statement on an `autocommit` connection took effect immediately. The
final test version does not depend on statistics at all (see above), but the observation
cost real time and is recorded so nobody re-learns it.

## MCP round trip against the cloud

`test_full_round_trip_through_call_tool` — all six tools through `mcp.call_tool()`, the same
entry point a real MCP client uses, against the cloud cluster:

claim → second claim denied naming the holder → remember a failure → recall finds it via a
differently-worded query → decide with mandatory provenance → status → release. Passed.

Two test-side fixes were needed for current `mcp` SDK behaviour (return shape of
`call_tool`, one content block per list element) and for the embedder selection in a
credential-less environment; both are commented in `tests/test_mcp_server.py`.

## What this run does NOT show

- No Bedrock embeddings: recall retrieval quality on the cloud run rests on the placeholder,
  i.e. lexical overlap only. The semantic claim remains open until the `aws`-marked test runs.
- No load or scale statement beyond 20 concurrent claimants. Nothing larger was measured,
  so nothing larger is claimed.

---

# 2026-07-26 — independent re-run on a second host

The run above was made on one machine. This section records an independent repeat on a
different host, against the same cloud cluster, to establish that the numbers are a property
of the code rather than of one developer's environment. Nothing was changed in the source
tree to obtain them: the tree was at commit `6beacb0` with a clean working directory before
and during the run.

## Environment

| | |
|---|---|
| Host | second workstation, Windows 11 Pro (build 10.0.26200) |
| Cluster | the same CockroachDB Cloud **Basic** cluster, AWS `eu-central-1` (Frankfurt) |
| Server version | `CockroachDB CCL v26.2.1 (x86_64-pc-linux-gnu, built 2026/05/21 21:07:32, go1.25.5)` |
| Connection | PostgreSQL wire, `sslmode=verify-full` |
| Client | Python 3.12.10, psycopg 3.3.4, pytest 9.1.1, `mcp` 1.28.1, boto3 1.43.56 |
| Embedder | offline placeholder (no AWS credentials on this host either) |
| Measured latency | `SELECT 1` round trip: min 39.8 ms, median 41.1 ms, max 98.9 ms |
| Date of run | 2026-07-26 |

The latency is roughly 2.5× the figure recorded for the first host (~15 ms). Same cluster,
same region — the difference is the client's network path, not the database. It is recorded
because the concurrency test's timing behaviour depends on it, and a reader comparing the two
runs should not have to guess why one took longer.

## Results

Offline, i.e. no cluster required — every `live`-marked test skips:

```
python -m pytest tests/ -q
73 passed, 45 skipped in 5.47s
```

Live, against the cloud cluster:

```
python -m pytest tests/ -m live -rs --timeout=600 -q
45 passed, 1 skipped, 72 deselected in 93.84s (0:01:33)
```

Lint:

```
ruff check .        (ruff 0.16.0)
All checks passed!
```

Both figures reproduce the first host exactly: 73 offline, 45 live. The three live numbers
add up to the same total as before (45 + 1 + 72 = 118 = 73 + 45), so no test moved between
markers and none was added or lost in between.

The one skip is the expected one, and the run was made with `-rs` so the reason is on the
record rather than inferred:

```
SKIPPED [1] tests\test_core_recall.py:345: embedder is not usable here:
            NoCredentialsError: Unable to locate credentials
```

That is `test_recall_with_the_real_embedder` (`aws`-marked, `tests/test_core_recall.py:324`),
which is still waiting for Bedrock credentials. The "what this run does NOT show" section
above therefore applies unchanged: retrieval quality here is lexical, not semantic.

## One environmental finding: the cluster CA has to be present per host

Worth recording because it cost time and will cost it again otherwise.

`sslmode=verify-full` makes libpq verify the server certificate against a root certificate,
and with no `sslrootcert` in the connection string libpq looks in one hard-coded place — on
Windows, `%APPDATA%\postgresql\root.crt`. On this host that file did not exist, so every
connection failed before reaching the database:

```
root certificate file "…/postgresql/root.crt" does not exist
```

Two things that did **not** work, recorded so they are not retried:

- `sslrootcert=system` → `SSL error: certificate verify failed`. The OpenSSL bundled with
  `psycopg[binary]` on Windows does not resolve to the operating system's certificate store,
  so "system" points at nothing usable.
- Downgrading to `sslmode=require` does connect, and is the tempting shortcut — but it
  disables certificate verification altogether. Numbers obtained that way would not be
  comparable with the run above, which was made under `verify-full`. It was not used, and
  no result in this document rests on it.

What worked: fetch the cluster's CA chain from the cluster's own certificate endpoint
(`https://cockroachlabs.cloud/clusters/<cluster-id>/cert`, unauthenticated — 2728 bytes, two
PEM blocks, an ISRG root chain), store it outside the repository, and name it explicitly with
`sslrootcert=<path>` appended to the DSN. Explicitly, and not by writing
`%APPDATA%\postgresql\root.crt`: that path is libpq's host-global default and would silently
change certificate validation for every other PostgreSQL client on the machine. A per-project
file that one connection string points at is the smaller footprint.

For anyone setting this up: the environment variable the suite reads is `ROSHAMBO_DSN`, and
appending `&sslrootcert=/path/to/root.crt` to the DSN from the cluster console is the whole
of the configuration. The certificate is not secret; the DSN it is appended to is.
