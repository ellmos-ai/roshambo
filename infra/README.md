# infra/ — deployment scripts

## Why plain boto3 + zip instead of AWS SAM

We considered AWS SAM (`sam build` / `sam deploy`) and chose a plain
`boto3` + `zipfile` script instead:

* **One fewer toolchain.** This lane already depends on `boto3` for the
  runtime code itself (`roshambo.embeddings.BedrockEmbedder`,
  `roshambo.aws.s3`, `roshambo.aws.worker`). Reusing it for deployment means the
  entire AWS side of this repo needs exactly one extra tool beyond Python:
  the AWS CLI's credentials (not even the CLI binary itself -- boto3 reads
  the same credential chain).
* **No local Docker.** `sam build --use-container` (the reproducible build
  path SAM recommends once you have compiled/binary dependencies) needs
  Docker. This build environment has no Docker installed (verified:
  `docker --version` -> command not found, 2026-07-25). A container-less
  `sam build` is possible but then loses the main reproducibility argument
  for SAM in the first place.
* **One function, one bucket, one role.** SAM's value is managing a
  multi-resource stack with CloudFormation's dependency graph and
  rollback semantics. `roshambo-worker` is a single Lambda function; a
  hand-written idempotent create-or-update script covers that without
  introducing a templating language (CloudFormation/SAM YAML) to debug on
  top of everything else.

**Honest tradeoff:** this script has no automatic rollback and no
`sam delete` equivalent -- `deploy_lambda.py teardown` is a manual
best-effort cleanup, not a transactional stack deletion. That is an
acceptable tradeoff for a single-function hackathon deployment, not a claim
that this approach scales to a larger service.

## Scripts

| Script | Does |
|---|---|
| `deploy_lambda.py` | Packages `src/roshambo` (+ the `aws` extra's third-party deps) into a zip, creates/updates the IAM execution role from `iam_trust_policy.json` + `iam_execution_policy.json`, and creates/updates the `roshambo-worker` Lambda function. Subcommands: `package`, `create-role`, `deploy`, `invoke`, `teardown`. |
| `iam_trust_policy.json` | Lambda's assume-role trust policy (standard, unmodified). |
| `iam_execution_policy.json` | Least-privilege execution policy: CloudWatch Logs (own log group only), `bedrock:InvokeModel`/`InvokeModelWithResponseStream` on exactly the two configured model ARNs, `s3:PutObject`/`GetObject` on exactly the configured bucket. No `s3:ListBucket`, no `s3:DeleteObject`, no wildcard resources. `deploy_lambda.py create-role` fills in the `{{AWS_REGION}}` / `{{AWS_ACCOUNT_ID}}` / `{{WORKER_BEDROCK_MODEL_ID}}` / `{{S3_BUCKET_NAME}}` placeholders from `RoshamboConfig` + `boto3.client("sts").get_caller_identity()` before creating the role. |
| `ccloud_provision.py` | Wraps the `ccloud` CLI (CockroachDB Cloud) to provision a cluster, print a connection string, and (best-effort) a service account and a backup. Subcommands: `check`, `create-cluster`, `list-clusters`, `connection-string`, `create-service-account`, `create-backup`. |

## What is and isn't verified about `ccloud`

Researched 2026-07-25 (sources in `docs/EVIDENCE-aws.md`). Confirmed, from
the CockroachDB Cloud AI-agents blog post and the official "Get Started
with the ccloud CLI" doc:

* Install: PowerShell download to `%appdata%\ccloud`, add to `PATH`.
* Auth: `ccloud auth login [--org <label>] [--no-redirect]`.
* Commands follow a **noun-verb** pattern.
* A **global `-o json` flag** exists on every command, meant for exactly
  this use case (an agent parsing structured output).
* Concretely verified command forms: `ccloud cluster create serverless
  <name> <region> --cloud AWS -o json`, `ccloud cluster list -o json`,
  `ccloud cluster connection-string <name> --database <db> --sql-user
  <user> -o json`.

**Not verified** -- the official reference page for the full command list
(`ccloud <noun> --help` equivalent) was not reachable during research, so
the exact flags for `service-account create` and any `backup` subcommand
are **inferred by the same noun-verb convention**, not confirmed against
documentation or a real CLI run (no `ccloud` binary is installed in this
environment either -- verified: `which ccloud` -> not found). Those two
`ccloud_provision.py` subcommands print the command they are about to run,
fail with the CLI's own error message rather than swallowing it, and their
docstrings say plainly that the exact syntax is unverified.

## Nothing in this directory has been executed against real AWS or ccloud

No AWS account/credentials and no `ccloud` binary are available in this
build environment (see `docs/EVIDENCE-aws.md`). These scripts are written
to fail cleanly and legibly when that's the case (missing binary, missing
credentials, missing config) rather than to silently no-op. They have not
created any billable resource.
