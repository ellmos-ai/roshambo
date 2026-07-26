# Roshambo demo web app

Branded "Roshambo" as of 2026-07-25 (the Python package/import path is unchanged --
still `roshambo`, see `docs/HANDOFF.md`). The frontend pulls its logo/favicon from
`assets/roshambo-*.png` at the repo root via a dedicated `/assets` mount in
`demo/app.py`, kept separate from `demo/static/` so that directory stays the single
shared source other surfaces (README, docs) also draw from.

A single FastAPI service plus a static HTML/CSS/vanilla-JS frontend (no
build step, no framework). Shows:

1. **Active claims** -- resource, holder (`agent_id`), **origin system**
   (`framework`/`host`), intent, acquired/expiry times. Polled every few
   seconds. This is where the collision (MANIFEST.md section 7, beat 1)
   becomes visible: whichever side won holds the claim, with its origin
   system shown as a badge -- see "The collision demo" below for how to
   actually produce one. `Roshambo` coordinates agents that don't know each
   other; a claims table that only ever shows one kind of framework would
   undersell exactly the thing being demonstrated, so the origin system is
   not decoration here.
2. **Recall search** -- a query box (`Roshambo.recall()`) with an outcome
   filter (`success` / `failure` / `abandoned` / `inconclusive`) and a
   distance-ranked hit list. This is the beat-3 moment the submission is
   actually about: a new agent finding a prior failure -- or a prior lost
   race -- before repeating it. A denied claim writes an `outcome="abandoned"`
   trail (see below), so a lost collision is itself something `recall()` can
   surface later, not just a line printed to a terminal.
3. **Turned Away** -- the agents that lost a race, each with the holder they were
   told about and what that holder is doing. This is beat 1's other half: a
   `ClaimDenied` is a return value, never a row, so what is listed are the
   `outcome="abandoned"` trails the losing workers wrote about themselves. They
   therefore outlive the winner releasing its lease, and they are readable no
   matter which embedder wrote them (no vector is involved). Fed by
   `/api/denials` → `demo/queries.py:recent_denials`.
4. **Status strip** -- `Roshambo.status()` counters (agents, active claims,
   trails, failures, facts).

The recall search is deep-linkable: `/?query=…&outcomes=failure&limit=5` fills the
form in and runs the search on load, which is how the screenshots in
`docs/screenshots/` were taken and how a beat can be replayed for a recording.

## The collision demo

```bash
# after installing (see "Run it" below) and exporting ROSHAMBO_DSN/ROSHAMBO_SWARM_ID
python demo/run_collision_demo.py \
    --resource "s3-prefix:<your-bucket>/agent-runs/demo-run/"
```

Races **two different agent runtimes** for one resource:

* the AWS side -- `roshambo.aws.worker.lambda_handler`, either invoked for real
  against an already-deployed `roshambo-worker` Lambda (`--lambda-mode invoke`,
  needs AWS credentials and `infra/deploy_lambda.py deploy` to have run
  first) or called in-process as a structural dry run
  (`--lambda-mode local-simulate`, the default -- prints a clear warning that
  this does **not** demonstrate a second machine).
* the non-AWS side -- `demo/local_agent_worker.py`, a separate script that
  never imports boto3 and registers under a different `framework`/`host` in
  the `agents` table (`schema/001_init.sql`), standing in for "some other
  agent system" rather than a second copy of the Lambda worker.

The resource defaults to an S3-*prefix*-shaped string
(`s3-prefix:<bucket>/agent-runs/<run-id>/`), not a filename -- Roshambo
coordinates arbitrary named resources, and a bucket prefix is a concrete
"not a file" example. Whichever side loses gets a `ClaimDenied` naming the
winner and its intent (printed to the terminal -- this is the "Absage" the
video is meant to show), and both workers' own code then writes an
`outcome="abandoned"` trail recording that, so the collision leaves a
permanent record in Roshambo's memory, not just console output.

Verified against a real, locally started CockroachDB v25.4.0 node in this
build environment (no AWS credentials were available in that same session,
so only `--lambda-mode local-simulate` was exercised end to end); see
`docs/EVIDENCE-aws.md` for the exact commands, output, and an honest account
of which side won in each run (it is not rigged -- both sides can win).

## Modes

Every API response carries a `"mode"` field:

* `"live"` -- connected to a real CockroachDB cluster via `ROSHAMBO_DSN`.
* `"mock"` -- no cluster reachable (unset `ROSHAMBO_DSN`, or the connection
  failed). Endpoints then return small, clearly-labelled example data --
  including the two-system collision story (one `aws-lambda-bedrock` claim,
  one `abandoned` trail for the `local-cli-agent` side that lost the race)
  -- so the UI itself stays inspectable without infrastructure. The frontend
  shows a visible banner whenever `mode !== "live"`; do not record the pitch
  video in mock mode.

## Run it

```bash
pip install -r demo/requirements.txt
pip install -e ".[aws]"        # or: PYTHONPATH=src

export ROSHAMBO_DSN="postgresql://<user>@<cluster-host>:26257/roshambo?sslmode=verify-full"
export ROSHAMBO_SWARM_ID="demo"
export ROSHAMBO_EMBEDDING_PROVIDER="placeholder"   # see "Which embedder" below

python demo/serve.py --dev
```

Then open `http://127.0.0.1:8000/`.

**Check the mode before you record anything.** The app falls back to mock data on any
connection failure rather than crashing, and a mock-mode recording is worthless:

```bash
curl http://127.0.0.1:8000/api/health     # expect {"mode":"live","detail":"connected"}
```

`demo/serve.py` hard-codes no port and no bind address, so the eventual host does not
force a code change:

| variable | meaning | default |
|---|---|---|
| `ROSHAMBO_DEMO_HOST` | bind address | `127.0.0.1` |
| `ROSHAMBO_DEMO_PORT` | port | `8000` |
| `PORT` | port, used if the above is unset (what managed hosts set) | — |
| `ROSHAMBO_DEMO_ROOT_PATH` | path prefix when behind a reverse proxy | `""` |

`--dev` adds auto-reload. The default bind is loopback with or without it: this endpoint
has no authentication (see "Not built here"), so it does not start listening on every
interface by accident. Everything the page fetches is a relative URL and a plain polled
`GET` — no WebSockets — so it also runs on a host that cannot hold a socket open, and under
a path prefix.

### If the connection fails with a certificate error

`sslmode=verify-full` makes libpq verify the cluster's certificate, and on a fresh machine
there may be no root certificate where it looks (`%APPDATA%\postgresql\root.crt` on
Windows, `~/.postgresql/root.crt` elsewhere). Download the CA chain from the cluster
console and point the DSN at it with `&sslrootcert=/path/to/root.crt` rather than
installing it host-wide — and do **not** work around it with `sslmode=require`, which
simply switches the check off. Recorded in `docs/EVIDENCE-cloud.md` (2026-07-26).

### Which embedder

`ROSHAMBO_EMBEDDING_PROVIDER=placeholder` selects `roshambo.memory.PlaceholderEmbedder`,
which hashes word tokens and character trigrams — the only offline embedder with any
retrieval signal, and the one the test suite uses. Its ranking is **lexical overlap, not
semantic similarity**. The `local` provider (`DeterministicEmbedder`) hashes the whole text
into uncorrelated vectors and makes `recall()` rank arbitrarily; `bedrock` is the real
semantic path and needs AWS credentials.

One consequence to know about: `roshambo.embeddings.get_embedder()` accepts only
`bedrock`/`local` (CONTRACT.md) and raises on `placeholder`, so `roshambo.aws.worker` — and
with it `run_collision_demo.py --lambda-mode local-simulate` — cannot run in the same
environment. Run that demo with `ROSHAMBO_EMBEDDING_PROVIDER=local` in a separate shell.

## Run it as a Lambda (the intended host)

The demo is meant to end up behind an **AWS Lambda Function URL** (decided 2026-07-26).
`demo/lambda_entry.py` is the entire adapter for that — one `Mangum(app, lifespan="off")`
over the same `demo.app:app` that runs locally:

```
Lambda handler:  demo.lambda_entry.handler
Local:           python demo/serve.py --dev
```

There is no second code path on purpose. Two properties of the app make that possible and
need to stay true:

* **It polls, it never holds a socket open.** Function URLs cannot do WebSockets. If a
  future change wants live updates, it has to stay on `GET` polling or the host choice
  breaks. Nothing in the current scenario needs sub-second latency — a lease lasts minutes.
* **Every URL the page builds is relative.** A Function URL serves at the domain root so
  the question does not arise there, but this is also what makes the reverse-proxy
  fallback host work.

### Not verified, and what it would take

Nothing has run in AWS: no account is attached to this project yet. What *is* verified is
that the handler answers Function-URL-shaped events correctly — including a base64 binary
asset and a query string, the two things adapters silently get wrong — in
`tests/test_demo_lambda_entry.py`. Open, in rough order of how likely each is to bite:

* **The deployment package.** It needs `mangum`, `fastapi`/`starlette`, `psycopg[binary]`
  and `src/roshambo`; it does **not** need `uvicorn`. `infra/deploy_lambda.py` packages
  the *worker* function and explains the manylinux-wheel convention that a demo package
  would follow — no such package is built here, because a zip that cannot be invoked
  proves nothing about whether the app runs in Lambda.
* **The database connection from Lambda.** TLS to the cluster over a Lambda ENI, and the
  psycopg wheel against the actual runtime, are only settled by a real invocation.
* **Cold start into mock mode.** `demo/app.py` connects once at import (so a warm
  container reuses the connection). If that first attempt fails, the container serves
  labelled mock data until a query errors and triggers a refresh. On a host with many
  short-lived containers that is worth an edge health check rather than in-process retry
  logic — the mock banner makes it visible either way, but the health endpoint is the
  thing to alarm on.
* **A deprecation in mangum itself.** mangum 0.21.0 calls `asyncio.get_event_loop()`,
  which warns on Python 3.12 (visible in the test run). Not our code, but it is the kind
  of thing that turns into an error on a future runtime.

## Play the demo script

`demo/run_story.py` plays the four beats of MANIFEST.md section 7 against the live cluster.
Run them one at a time with the browser open next to the terminal — the UI polls, so each
beat visibly changes it:

```bash
python demo/run_story.py --beat 1   # three agents collide; one lease, two informed refusals
python demo/run_story.py --beat 2   # the winner hits a dead end and records it
python demo/run_story.py --beat 3   # a new session finds that failure and picks another route
python demo/run_story.py --beat 4   # a holder goes silent; the lease lapses and is taken over
```

What to watch, beat by beat:

1. **Active Claims** gains one row, **Turned Away** gains two — same resource, both naming
   the winner and its intent. The winner keeps its lease on purpose so this state is
   photographable.
2. **Failures** goes to 1 and the claim disappears: the agent failed and handed the lease
   back rather than sitting on it.
3. Open the URL the script prints in its `reworded_query` field as a search, e.g.
   `/?query=roll+out+the+pending+schema+change+for+billing+on+the+live+database&outcomes=failure`.
   The script also prints the full ranked hit list, unfiltered and filtered.
4. **Active Claims** shows a *different* runtime holding the failover resource. The
   taking-over agent keeps its lease, so the app is left with something live on screen.

`--beat N` carries the winner, its lease and the failure trail between runs in
`demo/.story-state.json` (gitignored). `--all` runs all four in memory and writes nothing.

```bash
python demo/run_story.py --measure --rounds 10
```

repeats beat 1 and judges each round against the phase-4 acceptance criterion — exactly one
winner, exactly two denials, every denial naming the actual winner. It runs in its own
swarm (`<swarm_id>-measure`) so it does not disturb the counters the UI is showing.
Measured results: `docs/EVIDENCE-demo.md`.

## Known gap (see docs/HANDOFF.md, 2026-07-25)

`demo/queries.py` reads the `claims` table directly via `roshambo.db`'s
generic SQL helpers, because `roshambo.memory.Roshambo` has no bulk
"list active claims" method (only `who_has(resource)` for one resource at a
time, and `status()` for a count). Its second function, `recent_denials`,
reads `trails` directly for the same reason: `recall()` searches by vector,
and "the last n abandoned trails, newest first" is an ordinary query, not a
similarity question. This is a read-only, demo-only
workaround -- a `Roshambo.list_active_claims()` convenience method would let
this file go away. It now also `LEFT JOIN`s the `agents` table to resolve
each claim's `framework`/`host` (see `run_local_worker`'s and
`run_collision_demo`'s docstrings for how the join key -- reusing
`register_agent()`'s returned id as the claim's `agent_id` -- is kept
consistent); a claim made with an arbitrary hand-picked `agent_id` simply
shows no origin system rather than being dropped from the list.

## Not built here

* ECS Fargate hosting (MANIFEST.md marks it optional; `infra/` has no
  Fargate task definition -- deploying the demo container is a manual step
  for whoever runs the live recording, using any container host).
* Auth. This is a hackathon demo endpoint, not a production surface --
  do not expose it publicly without adding some.
