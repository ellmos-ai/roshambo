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
3. **Status strip** -- `Roshambo.status()` counters (agents, active claims,
   trails, failures, facts).

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

# to run against a real cluster:
export ROSHAMBO_DSN="postgresql://root@<cluster-host>:26257/roshambo?sslmode=verify-full"
export ROSHAMBO_SWARM_ID="demo"

python -m uvicorn demo.app:app --reload --port 8000
```

Then open `http://localhost:8000/`.

`python -m uvicorn` (not the bare `uvicorn` command) is deliberate: `-m`
adds the current directory to `sys.path`, which is what makes `demo` (a
plain directory, not installed as a package) importable as
`demo.app:app` / `from demo.queries import ...`.

## Known gap (see docs/HANDOFF.md, 2026-07-25)

`demo/queries.py` reads the `claims` table directly via `roshambo.db`'s
generic SQL helpers, because `roshambo.memory.Roshambo` has no bulk
"list active claims" method (only `who_has(resource)` for one resource at a
time, and `status()` for a count). This is a read-only, demo-only
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
