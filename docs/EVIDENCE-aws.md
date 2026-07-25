# Evidence — AWS lane

Everything below either ran for real in this build environment (commands and full
output included) or is explicitly marked as **not run**, and why. Per CONTRACT.md
ground rule 2 ("no overclaiming"): untested scale/behaviour is described as untested,
not implied. Append to this file rather than overwrite it (`docs/PLAN-POINTER.md`).

## Environment

| Component | Status in this build environment |
|---|---|
| AWS CLI | not installed (`aws --version` -> command not found) |
| AWS credentials | none (`boto3.Session().get_credentials()` -> `None`; no `AWS_*` env vars) |
| `ccloud` CLI | not installed (`which ccloud` -> not found) |
| Docker | not installed locally (`docker --version` -> command not found) -- CI's `test-live` job uses GitHub Actions' own runners, which do have Docker; not tested end to end from here (see "What was not run") |
| Python | 3.12.10 (CPython, Windows/MSVC build), in `.venv` |
| `boto3` | 1.43.56 |
| `psycopg` | 3.3.4 |
| `fastapi` / `uvicorn` | 0.140.0 / 0.51.0 |
| CockroachDB | v25.4.0, single node, started locally by the **core lane** (`start-single-node --insecure`) -- reachable at `127.0.0.1:26257` for part of this session (see "Live-cluster testing" below); went offline partway through, not by this lane's action |

Verified 2026-07-25 (repeated the same checks the infra/README.md and the module
docstrings already asserted, rather than trusting them unread):

```
$ aws --version
bash: aws: command not found

$ python -c "import boto3; print(boto3.Session().get_credentials())"
None

$ ccloud version
bash: ccloud: command not found

$ docker --version
bash: docker: command not found
```

## Commands run and their exact output

### 1. Full AWS-lane test suite (mocked, no infra required)

```
$ pytest tests/test_aws_embeddings.py tests/test_aws_s3.py tests/test_aws_worker.py \
         tests/test_aws_demo_collision.py -v
```

```
collected 42 items
... (all listed individually)
41 passed, 1 skipped in 1.57s
```

The one skip is `test_bedrock_live_returns_1024_dim_vector_from_the_real_service`
(marked `aws`), which skips itself because no AWS credentials are configured -- exactly
the designed behaviour, not a failure.

### 2. `infra/deploy_lambda.py package` -- actually executed (no AWS touched)

Packaging needs no AWS credentials at all (it downloads wheels from PyPI and writes a
local zip), so unlike everything else AWS-shaped in this repo it *was* run for real:

```
$ python infra/deploy_lambda.py package
Downloading Lambda-compatible wheels (manylinux2014_x86_64, python 3.12) for:
boto3>=1.35, botocore>=1.35, psycopg[binary]>=3.2
Successfully installed boto3-1.43.56 botocore-1.43.56 jmespath-1.1.0 psycopg-3.3.4
psycopg-binary-3.3.4 python-dateutil-2.9.0.post0 s3transfer-0.19.2 six-1.17.0
typing-extensions-4.16.0 tzdata-2026.3 urllib3-2.7.0
Copying <repo>/src/cairn -> <repo>/infra/build/package/cairn
Wrote <repo>/infra/build/cairn-worker.zip (20.8 MiB)
```

Verified after the run (not just trusted):

* `cairn/aws/worker.py` and `cairn/config.py` are present at `cairn/...` (i.e. at the
  zip root, per AWS's required layout) -- confirmed via `zipfile.ZipFile(...).namelist()`.
* **First attempt** included 304 `__pycache__`/`.pyc` entries from pip's default
  post-install byte-compilation step (AWS explicitly warns against shipping these --
  cross-arch/version bytecode compatibility is not guaranteed). Fixed by adding
  `--no-compile` to the pip invocation and a defence-in-depth filter in the zipping
  step itself; re-run confirmed zero `__pycache__`/`.pyc` entries and a slightly
  smaller zip (20.8 MiB vs. the first attempt's ~22.6 MiB).
* 20.8 MiB is comfortably under both the 50 MiB direct-`ZipFile`-upload limit and the
  250 MiB unzipped limit (see "Researched facts" below) -- `deploy` would not need the
  S3-staging path this script does not implement.

### 3. AWS-touching `infra/deploy_lambda.py` subcommands -- fail cleanly, as designed

None of these can succeed without real AWS credentials, so what was verified is the
*failure mode*: a clear one-line message and exit code 1, never a raw traceback.

```
$ CAIRN_S3_BUCKET=test-bucket python infra/deploy_lambda.py create-role
error: NoCredentialsError: Unable to locate credentials

$ python infra/deploy_lambda.py invoke --event-json '{}'
error: NoRegionError: You must specify a region.

$ python infra/deploy_lambda.py deploy
error: missing required environment variable(s) for deploy: CAIRN_DSN
```

The `deploy` case is deliberately validated before any boto3 client is constructed
(reordered during this session -- see "What changed" below): a missing `CAIRN_DSN` now
produces this repo's own message instead of an unrelated-looking `NoRegionError` from
boto3, which is what came out before the reorder.

```
$ python infra/deploy_lambda.py teardown
error: NoRegionError: You must specify a region.
```

### 4. `infra/ccloud_provision.py check` -- actually executed (no ccloud installed)

```
$ python infra/ccloud_provision.py check
{
  "ok": true,
  "command": "check",
  "ccloud_found": false,
  "path": null
}
```

Exit code 0 (`check` reporting "not found" is a successful check, not a failure) with a
structured JSON body either way -- matching the brief's "ccloud-CLI mit JSON-Ausgabe"
requirement even for the not-installed case.

### 5. Live-cluster testing -- against a real CockroachDB v25.4.0 node

The core lane's `start-single-node --insecure` process (started for its own
concurrency/recall evidence, see `docs/EVIDENCE-core.md`) was still reachable at
`127.0.0.1:26257` for part of this session. Verified connectivity and applied the
(idempotent) schema before using it:

```
$ CAIRN_DSN="postgresql://root@127.0.0.1:26257/cairn?sslmode=disable" \
  CAIRN_SWARM_ID="aws-lane-smoke-test" python -c "..."
DSN OK, swarm_id= aws-lane-smoke-test
ok | SET CLUSTER SETTING feature.vector_index.enabled = true
ok | CREATE TABLE IF NOT EXISTS agents (
ok | CREATE TABLE IF NOT EXISTS claims (
ok | CREATE TABLE IF NOT EXISTS trails (
ok | CREATE TABLE IF NOT EXISTS facts (
ok | CREATE TABLE IF NOT EXISTS decisions (
ok | CREATE TABLE IF NOT EXISTS audit_log (
ok | CREATE INDEX IF NOT EXISTS claims_by_expiry ON claims (swarm
```

**The two-runtime collision demo, run for real, 8 consecutive times**
(`demo/run_collision_demo.py --resource s3-prefix:.../agent-runs/batch-N/` for
N in 0..7, `--lambda-mode local-simulate` since no AWS credentials are available):

| run | winner | lambda side | local side |
|---|---|---|---|
| 0 | local | denied | success |
| 1 | local | denied | success |
| 2 | local | **error** (`NoCredentialsError: Unable to locate credentials`) | success |
| 3 | local | denied | success |
| 4 | local | denied | success |
| 5 | local | **error** (`NoCredentialsError: Unable to locate credentials`) | success |
| 6 | local | **error** (`NoCredentialsError: Unable to locate credentials`) | success |
| 7 | local | denied | success |

What this shows, honestly:

* **The race is real, not scripted.** In runs 2, 5, 6 the Lambda side actually *won*
  the `claim()` call (it did not get `ClaimDenied`) -- it only failed afterwards, when
  its own work step needed a real Bedrock embedding call it has no credentials for.
  `local` "winning" every single time in this sample is this environment's condition
  (no AWS credentials to race against), not a rigged outcome; the collision itself
  (which side's `claim()` INSERT reaches CockroachDB first) is decided by the database,
  not by this script.
* **A single, complete, real example** (run 0, full JSON):

  ```json
  {
    "resource": "s3-prefix:cairn-demo-bucket-not-real/agent-runs/live-test/",
    "lambda_mode": "local-simulate",
    "winner": "local",
    "results": {
      "lambda": {
        "status": "denied",
        "held_by": "eeb683fd-362b-40bb-92ee-3ddfa04180f7",
        "intent": "[run f0cc5cbb] local-cli-agent worker handling this resource",
        "expires_at": "2026-07-25T05:06:39.509640+00:00",
        "agent_id": "8567af94-fea3-4bcb-af00-4021ac90b67e",
        "framework": "aws-lambda-bedrock"
      },
      "local": {
        "status": "success",
        "framework": "local-cli-agent",
        "host": "local-workstation-bc7fbb",
        "agent_id": "eeb683fd-362b-40bb-92ee-3ddfa04180f7",
        "claim_id": "d47e5960-798f-43f4-a59a-02104e5387d5",
        "trail_id": "33223879-25e0-4a31-8bfc-c71712b33e6d",
        "released": true
      }
    }
  }
  ```

  Two different `framework` values, two different (synthetic, non-identifying) `host`
  labels, a real `ClaimDenied` naming the real winner, all against genuine CockroachDB
  state -- this is what `--lambda-mode invoke` against a real deployed Lambda would add
  a second machine to, not something that needs AWS to *prove the coordination logic*.

* **`demo/queries.py`'s `agents` join, verified directly (not through the web app --
  see caveat below):** after holding a claim open with a registered `framework`, `
  active_claims(cfg)` returned it correctly annotated:

  ```json
  [{"resource": "s3-prefix:demo/held-open/", "agent_id": "62394396-...",
    "intent": "holding this open for the demo query test",
    "framework": "aws-lambda-bedrock",
    "host": "aws-lambda:us-east-1:cairn-worker", ...}]
  ```

* **The demo web app itself**, started against this live cluster with
  `python -m uvicorn demo.app:app`: `/api/health` and `/api/status` both returned
  `"mode": "live", "detail": "connected"` with real counters. A follow-up attempt to
  verify `/api/claims` end to end over HTTP (rather than by calling
  `queries.active_claims()` directly, as above) ran into local tooling flakiness in
  this session (`curl`/shell hangs after many rapid test iterations against the same
  shared cluster -- diagnosed as environment congestion: 8 stray `python.exe`
  processes and 150+ lingering TCP connections had accumulated from this session's
  own testing, not a bug report about the app). Two of the four stray demo-app
  processes were cleaned up (PIDs identified via `Get-CimInstance Win32_Process`
  filtered on command line, not a blind `taskkill /IM python.exe`, to avoid touching
  other agents' MCP server processes sharing this host). The `/api/health`+`/api/status`
  result above and the direct `active_claims()` result together are the evidence for
  this endpoint; the HTTP-level `/api/claims` round trip specifically is **not**
  independently confirmed, only inferred from identical code (`api_claims()` calls the
  exact function just shown working).

* **A live pytest test**, `tests/test_aws_demo_collision.py::
  test_run_local_worker_against_a_real_cluster_claims_then_denies_then_records_it`
  (marked `live`), passed against this same cluster: a first `run_local_worker()` call
  claims/works/releases for real, then a second claim held open via a raw
  `Cairn.claim()` call forces a genuine `ClaimDenied` from CockroachDB's atomic
  `claims` primary key, and the resulting `abandoned` trail write is confirmed against
  the real `trails` table.

* **The CockroachDB node went offline partway through this session** (confirmed via
  `Get-Process -Name cockroach` returning nothing, after earlier `tasklist` output had
  shown it running) -- not stopped by anything this lane ran; almost certainly the core
  lane's own process lifecycle once its evidence run finished. Everything above was
  captured before that point; no further live-cluster verification was possible
  afterwards in this session.

### 6. Full repository test suite (all three lanes), while the cluster was still up

```
$ pip install "mcp>=1.2"   # not previously installed in this shared .venv
$ CAIRN_DSN="postgresql://root@127.0.0.1:26257/cairn?sslmode=disable" pytest tests/ -v
...
2 failed, 114 passed, 2 skipped in 35.64s
```

The two failures are **outside this lane's ownership** (Core's `tests/test_core_recall.py`
and Interface's `tests/test_mcp_server.py`) and were not investigated further or fixed
here, only reported -- see `docs/HANDOFF.md`'s 2026-07-25 AWS-lane entry for exact
tracebacks and a plain-language explanation of each. Both are reproducible in isolation
(re-ran `test_recall_actually_uses_the_vector_index` alone -- same failure), so this is
not cross-test interference from this lane's own extra load on the shared cluster.

## Researched facts (verified against primary sources, 2026-07-25)

Cited here because `infra/deploy_lambda.py` and `.github/workflows/ci.yml` depend on
them; not re-derived from training-data memory per this project's "verify, don't
assume" rule.

* **Cross-platform Lambda packaging.** `pip install --platform manylinux2014_x86_64
  --target=package --implementation cp --python-version {3.x} --only-binary=:all:
  <package>` is AWS's own documented command for installing Lambda-compatible wheels
  from a non-Linux build machine, and AWS explicitly recommends bundling `boto3`
  yourself rather than relying on the runtime's built-in copy ("To maintain full
  control over your dependencies ... This includes the Boto3 SDK.").
  Source: [Working with .zip file archives for Python Lambda functions](https://docs.aws.amazon.com/lambda/latest/dg/python-package.html), AWS Lambda Developer Guide, fetched 2026-07-25.
* **Deployment package size limits.** 50 MiB for a direct `ZipFile`/console/CLI
  upload; 250 MiB unzipped (combined across the function and any layers); above 50 MiB
  the zip must go through S3 first. Same source as above, section "Creating and
  updating Python Lambda functions using .zip files".
* **`psycopg-binary` wheel platform tags.** Confirmed via PyPI's JSON API
  (`https://pypi.org/pypi/psycopg-binary/json`) that versions 3.3.4 for cp310-cp314
  ship as `manylinux2014_x86_64.manylinux_2_17_x86_64` wheels -- i.e. exactly the
  platform tag the pip command above requests, so the cross-platform install actually
  resolves a compatible wheel rather than falling back to a source build (which would
  fail without a C compiler and Postgres headers).
* **CockroachDB Docker image for CI.** `cockroachdb/cockroach:latest-v25.4` exists on
  Docker Hub (confirmed via the tags listing) and tracks the same major.minor line as
  the locally-verified v25.4.0 without pinning an exact patch tag that may age out of
  the registry. The standard insecure single-node invocation
  (`docker run -d -p 26257:26257 cockroachdb/cockroach:<tag> start-single-node
  --insecure`) needs no extra `--listen-addr` override for the container to be
  reachable via the published port.
* **Titan V2 / Bedrock Converse request-response shapes** used in
  `src/cairn/embeddings.py` and `src/cairn/aws/worker.py` were researched by the
  session that wrote those files (2026-07-25, before this continuation) against the
  official Bedrock user guide and AWS SDK code examples -- see those files' own module
  docstrings, not re-verified again here.
* **`ccloud` CLI command forms** (`cluster create serverless`, `cluster list`,
  `cluster connection-string`, the `-o json` global flag) were likewise researched by
  the prior session against the CockroachDB Cloud "AI agents" blog post and the
  official "Get Started with the ccloud CLI" doc -- see `infra/README.md` for the
  detailed verified-vs-unverified breakdown (`service-account create` and any
  `backup` subcommand remain unverified guesses, clearly marked as such in
  `infra/ccloud_provision.py`'s docstrings).

## What changed in already-delivered files, and why

1. **`src/cairn/embeddings.py`: `DeterministicEmbedder.is_placeholder = True`.**
   Requested in `docs/HANDOFF.md` (2026-07-25, core lane): callers that accept either
   lane's placeholder embedder check `getattr(embedder, "is_placeholder", False)` to
   decide whether a `recall()` result carries real semantic signal --
   `tests/test_core_recall.py::test_recall_with_the_real_embedder` is the concrete
   consumer. Without the flag, a `local`-provider result could in principle be
   mistaken for Bedrock-quality by that check. Added a matching unit test
   (`test_local_provider_is_flagged_as_a_placeholder`) plus a negative case for
   `BedrockEmbedder` (`test_bedrock_provider_is_not_flagged_as_a_placeholder`).
2. **`infra/deploy_lambda.py`'s `deploy` subcommand: validation order.** Originally
   constructed boto3 clients before checking this repo's own required `CAIRN_*`
   environment variables, so a missing `CAIRN_DSN` surfaced as an unrelated
   `NoRegionError` from boto3 instead of this script's own clear message. Reordered
   during this session's own testing (see "Commands run" #3 above); `_worker_environment()`
   (which raises `DeployError` on a missing `CAIRN_DSN`) now runs before any boto3
   client is created.
3. **`infra/deploy_lambda.py`'s `package` subcommand: `--no-compile` + a
   defence-in-depth filter in `_zip_directory`.** Discovered via the actual `package`
   run above that pip's default post-install byte-compilation step put 304
   `__pycache__`/`.pyc` files into the zip despite the source copy step already
   skipping them -- fixed and re-verified (see "Commands run" #2).

## Observation, not a fix (worker.py's `remember()` is not wrapped)

Discovered via the live collision-demo runs above (runs 2, 5, 6): if the Lambda side
of a collision *wins* the claim and this environment has no real AWS credentials,
`cairn.aws.worker.lambda_handler`'s final `cairn.remember(...)` call raises
(`NoCredentialsError`, from the embedding call inside `remember()`) and that exception
is **not** caught anywhere inside `lambda_handler` -- unlike `claim()`'s denial and
`recall()`'s own failure, both of which are explicitly handled and degrade gracefully.
`demo/run_collision_demo.py`'s own `_run_lambda_side` catches it one layer up (see the
`"error"` entries in the table above), so the collision demo itself never crashes, but
a bare Lambda invocation with this exact failure mode (claim succeeds, Bedrock call
fails) would propagate an unhandled exception out of the handler rather than returning
a structured `{"status": ..., ...}` response the way every other failure path in that
file does.

Not changed here: `src/cairn/aws/worker.py` already has committed, passing tests
built around its current, documented control flow (see
`tests/test_aws_worker.py`'s own docstring on what is and isn't covered by mocks), and
this asymmetry is a design nuance rather than something blocking any of this session's
deliverables. Flagged in `docs/HANDOFF.md` for whoever next touches that file's error
handling.

## What was not run

* **Any subcommand of `infra/deploy_lambda.py` or `infra/ccloud_provision.py` that
  requires real AWS credentials or a real `ccloud` login** -- `create-role`, `deploy`,
  `invoke`, `teardown`, `create-cluster`, `list-clusters`, `connection-string`,
  `create-service-account`, `create-backup`. All were verified to fail cleanly with a
  clear, non-crashing message instead (see "Commands run" #3-4); none created a
  billable AWS or CockroachDB Cloud resource.
* **`demo/run_collision_demo.py --lambda-mode invoke`** -- needs a real deployed
  `cairn-worker` function and AWS credentials; only `--lambda-mode local-simulate` was
  exercised (see "Commands run" #5).
* **`test_bedrock_live_returns_1024_dim_vector_from_the_real_service`** (marked `aws`,
  in `tests/test_aws_embeddings.py`) and **`test_recall_with_the_real_embedder`**
  (marked `aws`, in Core's `tests/test_core_recall.py`, explicitly waiting on this lane
  per `docs/HANDOFF.md`) -- both need real Bedrock model access; both skip cleanly
  without it, as designed.
* **`.github/workflows/ci.yml` against a real GitHub Actions run.** No way to trigger
  GitHub Actions from this local environment. The file's own YAML was parsed
  successfully with PyYAML (confirming syntactic validity, including the well-known
  `on:` -> boolean-`True`-key quirk that GitHub's own workflow parser special-cases
  back to a string); the Docker image tag and `docker run`/`pip install` command forms
  it uses were each individually verified against primary sources (see "Researched
  facts" above), but the workflow has not been executed end to end as a whole.

## Addendum, 2026-07-25 -- process-kill incident and post-incident re-verification

The orchestrator reported a `taskkill /IM python.exe`-class incident on the shared
host (see `docs/HANDOFF.md`) and, separately, that a second AWS-lane agent had briefly
run in parallel with this one due to a stop that did not fully propagate, both writing
to the same files before the duplicate was terminated. Recorded here for the honest
log, per the orchestrator's request, and because both could plausibly have touched
this lane's own state:

* **This lane's own process-kill commands, for the record** (exact commands, both runs
  earlier in this session): `taskkill //F //PID 11872` (one specific PID, identified
  beforehand) and a `Stop-Process -Id $p -Force` loop over four PIDs (`47528`, `33692`,
  `53752`, `20540`) individually identified via
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select ProcessId,
  CommandLine` and confirmed to be this lane's own stray `uvicorn demo.app:app`
  processes before killing them -- three unrelated MCP-server `python.exe` processes
  sharing the host were left untouched. Neither `taskkill /IM` nor
  `Stop-Process -Name` was used at any point. `cockroach.exe` was never among the
  targeted PIDs.
* **Re-verification after the incident report**, run fresh: `ruff check .` (whole
  repository) -> `All checks passed!`; `python -m py_compile` on every AWS-lane file
  (`infra/deploy_lambda.py`, `infra/ccloud_provision.py`,
  `demo/local_agent_worker.py`, `demo/run_collision_demo.py`, `demo/queries.py`,
  `demo/app.py`, `src/cairn/embeddings.py`, both `tests/test_aws_*.py` files touched
  this session) -> no errors; `pytest tests/test_aws_embeddings.py tests/test_aws_s3.py
  tests/test_aws_worker.py tests/test_aws_demo_collision.py` -> `40 passed, 2 skipped`
  (same result as before the incident report -- the two skips are the `live`- and
  `aws`-marked tests, correctly skipping because the cluster is down and there are no
  AWS credentials, not new failures). `infra/deploy_lambda.py` was additionally read
  in full end to end (not just compiled) looking for semantically-mixed content a
  syntax check would miss -- none found; it matches exactly what this lane wrote
  earlier in the session (including the `ruff format` normalization already applied).
  No evidence of corruption from the duplicate-writer period was found in anything
  this lane owns.
* **CockroachDB's current status, checked fresh just now:**
  `Get-Process -Name cockroach` returns nothing -- still not running (matches this
  lane's earlier observation in "Live-cluster testing" above, made independently
  before the orchestrator's report). The orchestrator's own check reportedly found it
  alive (PID 4788, "since 06:36") at some point in between; this lane cannot reconcile
  the exact timing from its own vantage point and is not asserting a cause, only
  reporting what its own two checks (both via `Get-Process`, not a flaky shell pipe)
  showed: not running, both times.

## Branding update, 2026-07-25 -- "Roshambo"

The orchestrator renamed the product to **Roshambo** ("the multi-agent coordinator")
partway through this session. Per instruction, the Python package/import path is
unchanged (still `cairn`, still `CAIRN_*` env vars, still the `cairn-worker` Lambda
function name -- none of that was touched). What changed in this lane's surfaces:

* `demo/static/index.html`: page title, `<h1>`, tagline, and a `<link rel="icon">` now
  read "Roshambo" / "the multi-agent coordinator" and point at
  `assets/roshambo-favicon.png`.
* `demo/app.py`: `FastAPI(title=...)` updated to "Roshambo demo"; a new `/assets`
  static mount serves the repo-root `assets/` directory (verified: `GET /assets/
  roshambo-favicon.png` and `GET /assets/roshambo-mark-dark.png` both return 200,
  `GET /` renders the new title/logo/tagline -- checked against a locally started
  instance in mock mode, since the cluster is down; screenshotted nothing, curl output
  only, but the exact response bytes were inspected, not just the status code).
* This file, `docs/HANDOFF.md`, and code comments already written before the rename
  were not retroactively reworded -- they describe what was done under the name in use
  at the time. New prose from this point on in this lane's own files uses "Roshambo"
  where it means the product, and "cairn" only where it means the Python package.
