# Evidence — the demo, and the phase-4 acceptance number

What the demo web app was made to do, run live against the CockroachDB Cloud cluster on
2026-07-26. Everything below was executed and its output copied in; nothing is estimated,
and the one thing that did not come out as hoped is written down as such (§4).

## Environment

| | |
|---|---|
| Cluster | CockroachDB Cloud **Basic** (serverless), AWS `eu-central-1` (Frankfurt) — the same cluster as `EVIDENCE-cloud.md` |
| Client | Python 3.12.10, psycopg 3.3.4, FastAPI 0.140.0, uvicorn 0.51.0 |
| Swarm | `demo-2026-07-26` (story), `demo-2026-07-26-measure` (acceptance run) |
| Embedder | `ROSHAMBO_EMBEDDING_PROVIDER=placeholder` → `roshambo.memory.PlaceholderEmbedder` |
| App mode | `GET /api/health` → `{"mode":"live","detail":"connected"}` — checked before every screenshot |
| Date | 2026-07-26, ~17:20–17:30 local (15:20–15:30 UTC) |

**About that embedder.** `PlaceholderEmbedder` hashes word tokens and character trigrams,
so what it ranks by is **vocabulary overlap, not semantic similarity** — the same embedder
`tests/conftest.py` uses. Amazon Bedrock's Titan embeddings are the semantic path and have
still not been exercised (`test_recall_with_the_real_embedder` remains the one skipped
test, see `EVIDENCE-cloud.md`). Nothing in this document should be read as a claim about
semantic retrieval.

The other offline embedder, `local` / `roshambo.embeddings.DeterministicEmbedder`, hashes
the whole text into uncorrelated vectors and makes `recall()` rank arbitrarily; it is
unsuitable here. Note that `roshambo.embeddings.get_embedder()` accepts only
`bedrock`/`local` (CONTRACT.md) and raises on `placeholder`, so `roshambo.aws.worker` —
and therefore `demo/run_collision_demo.py`'s Lambda side — cannot run under the
environment used here. That is why `demo/run_story.py` builds its workers directly on
`Roshambo(cfg)`; the two demos are run separately.

## 1. Acceptance: three concurrent workers, exactly one winner

MANIFEST.md phase 4 asks for "drei gleichzeitige Worker → genau ein Gewinner". Measured
over ten independent rounds:

```
python demo/run_story.py --measure --rounds 10

round 1/10: 1 winner, 2 denials, ok=True (local-cli-agent, 0.9s)
round 2/10: 1 winner, 2 denials, ok=True (notebook-agent, 1.73s)
round 3/10: 1 winner, 2 denials, ok=True (mcp-agent, 0.83s)
round 4/10: 1 winner, 2 denials, ok=True (notebook-agent, 0.77s)
round 5/10: 1 winner, 2 denials, ok=True (notebook-agent, 0.73s)
round 6/10: 1 winner, 2 denials, ok=True (local-cli-agent, 0.81s)
round 7/10: 1 winner, 2 denials, ok=True (notebook-agent, 0.72s)
round 8/10: 1 winner, 2 denials, ok=True (notebook-agent, 0.73s)
round 9/10: 1 winner, 2 denials, ok=True (notebook-agent, 0.97s)
round 10/10: 1 winner, 2 denials, ok=True (local-cli-agent, 0.71s)

"rounds": 10, "rounds_passed": 10, "rounds_failed": 0, "ok": true,
"wins_by_framework": {"notebook-agent": 6, "local-cli-agent": 3, "mcp-agent": 1}
```

**10 of 10 rounds passed.** Each round is judged on more than the headline number, because
"exactly one winner" alone would also be satisfied by a system that granted one lease and
then lied to the others. Per round the run asserts:

1. exactly **1** granted lease,
2. exactly **2** denials,
3. every denial naming the **actual winner** as the holder — a denial naming anyone else
   would mean two leases existed at once,
4. every denial reporting the winner's **own intent** verbatim.

The winner is not fixed: three different runtimes won across the ten rounds (6 / 3 / 1).
A fresh resource string is used per round, so no round can win by inheriting a lease from
the previous one.

**What "concurrent" means here, precisely.** Three threads in one process, each with its
own `Roshambo` instance and therefore its own psycopg connection (`Roshambo` is documented
as not thread safe), meeting at a `threading.Barrier` after registration so that the only
contended step is `claim()`. Three concurrent transactions against one cluster is what
decides the winner. Three separate OS *machines* would be a stronger statement and was not
done. The 20-way version of the same property is covered by
`tests/test_core_concurrency.py` in the live suite.

## 2. The four beats, live

Run one beat at a time (`demo/run_story.py --beat N`) against the live app so the polling
UI could be photographed between beats. Screenshots: `docs/screenshots/`.

### Beat 1 — collision

`s3-prefix:roshambo-demo-bucket/agent-runs/story-3797dc52/`, three runtimes at once:

| runtime | host | result | claim latency |
|---|---|---|---|
| `mcp-agent` | `mcp-gateway-eu-central-1` | **granted**, lease to 15:28:21.402 UTC | 123.0 ms |
| `local-cli-agent` | `on-prem-batch-node-3` | denied — held by `e54e7f9e…c974` | 173.4 ms |
| `notebook-agent` | `analytics-notebook-07` | denied — held by `e54e7f9e…c974` | 177.1 ms |

Both denials carried the holder's id, the holder's intent (`apply the pending billing
schema migration (mcp-agent run)`) and the lease expiry, and each loser wrote its own
`outcome='abandoned'` trail — which is what the UI's *Turned Away* panel lists. The
collision therefore survives in memory rather than only in a terminal.

### Beat 2 — the winner fails, and says so

The holder ran into a dead end and recorded it before handing the lease back:

* `approach`: *apply the pending billing schema migration (mcp-agent run), run straight
  against the primary with no lock timeout*
* `outcome`: `failure`
* `evidence`: *blocked behind a long-running analytics report holding a lock on
  billing_invoices; aborted after 30s with SQLSTATE 55P03 (lock_not_available). The
  migration never applied and the table was left untouched.*
* `release()` → `true`

### Beat 3 — a new session asks the same thing in different words

A newly registered agent (`local-cli-agent` on `on-prem-batch-node-7`, no context) queried:

> `roll out the pending schema change for billing on the live database`

against the failure it never saw being written:

> `apply the pending billing schema migration (mcp-agent run), run straight against the
> primary with no lock timeout`

Result — see §4 for the honest reading of it:

| filter | rank of the failure | distance |
|---|---|---|
| none | **3 of 3** | 0.6113 |
| `outcomes=['failure']` | **1 of 1** | 0.6113 |

Full unfiltered ranking: `abandoned` 0.5821 · `abandoned` 0.5838 · `failure` 0.6113 — the
two trails ahead of it are the losers of beat 1, which are also about this job.

The agent then acted on the find, which is the part the submission is actually about:

```
decide(question  = "How should the pending billing schema change be rolled out?",
       choice    = "Set a short lock_timeout and retry on 55P03 instead of migrating
                    straight against the primary",
       rationale = "recall(...) returned trail 860817ba… at distance 0.6113 with
                    outcome=failure: the direct route already blocked behind a
                    long-running report and aborted with 55P03. Repeating it would fail
                    the same way, so this run takes the other route.",
       provenance= "agent-inferred")
reinforce(860817ba…) -> strength 2.0
```

### Beat 4 — a lease lapses and is taken over

One agent took a 6-second lease on `…/agent-runs/failover-d049544e/` and then went silent:
no `release()`, no `heartbeat()`. A second agent asked for the same resource repeatedly.

| | |
|---|---|
| First attempt, lease still valid | **denied** (as it must be) |
| Lease expired at | `15:26:02.060815 UTC` |
| Taken over at | `15:26:02.296542 UTC` |
| Delay | **0.236 s** |
| Attempts until granted | 6 (1 s polling) |

Both timestamps are server-side: the takeover time is derived from the new lease's
`expires_at` minus the TTL it was granted with, so nothing here depends on the client
machine's clock. Nothing cleaned up after the vanished agent — the lease expired because
it was written with an expiry, and the next `claim()` after that point simply succeeded.

## 3. Screenshots

Four PNGs in `docs/screenshots/`, taken with headless Chrome (`--headless=new
--screenshot`) against the running app in `"mode":"live"`. No mock data, no compositing,
no editing. See `docs/screenshots/README.md` for what each one shows.

## 4. What did not come out as hoped

**The failure does not lead the unfiltered ranking.** MANIFEST.md phase 2 asks for the
reworded query to find the failure at *rank 1*. Measured here, it comes back at rank 3
unfiltered and at rank 1 only once the search is restricted to failures. Two runs, same
outcome.

The cause is a property of the placeholder embedder, not of the vector index: it scores by
shared word tokens and trigrams over unit-normalised vectors, which favours *short* texts.
The two `abandoned` trails ahead of it are short (topic + intent + a one-line denial note),
while the failure trail carries a long evidence sentence full of vocabulary the query does
not contain (`analytics report`, `billing_invoices`, `SQLSTATE 55P03`). Diluting a match
with the very error text a later agent most needs is exactly the trade-off a real embedding
model exists to resolve.

The query was **not** re-tuned until the beat looked better. Two things follow:

* Phase 2's rank-1 criterion stays **unproven** until `test_recall_with_the_real_embedder`
  runs against Bedrock. Until then, `recall()` must not be described as semantic anywhere
  (standing constraint from `docs/HANDOFF.md`).
* The beat's actual claim — *memory changed what the agent did* — holds regardless of rank:
  the agent retrieved the failure, cited it by id and distance, and chose the other route.
  All three hits it got back were relevant; none of them was noise.

**Not exercised here.** Real AWS Lambda invocations, S3 artifacts and Bedrock embeddings —
no AWS account is attached to this project yet. The three racers are three agent runtimes,
not three machines. Public hosting of the demo is deliberately out of scope; the app is
built so the choice of host stays open (no hard-coded port, path-prefix tolerant, polling
rather than WebSockets).
