# Roshambo Agent Instructions

## Scope and required context

- AWS tooling configured for this workstation is scoped to this repository.
  Do not use it for another project unless the user explicitly expands the scope.
- Before changing Roshambo, read `README.md`, `CONTRACT.md`, `docs/HANDOFF.md`,
  `docs/NEXT-RUN.md`, and the relevant evidence file.
- Lock readback is explicit: first inventory active repository locks (`LOCK*.txt`) at
  the repository root. As of 2026-08-01 there is no mandatory root `LOCK.txt` in this
  clone. A missing `LOCK.txt` is therefore not a license to reset or overwrite work.
  Treat TaskPLAN/Lockmaster task assignment, repository lock files, and lane ownership
  as separate signals; certify foreign work before mutation.
- Respect the ownership boundaries in `CONTRACT.md`. If a required edit belongs
  to another lane, record the request in `docs/HANDOFF.md`.
- Preserve unrelated changes. Do not commit unless the current project rules or
  the user explicitly authorize it.

## Roshambo AWS boundaries

- Do not create, update, invoke, or delete billable AWS resources merely to
  inspect or validate the repository. A live AWS action requires an explicit
  task from the user.
- Never deploy Roshambo with AWS root credentials. Use a dedicated,
  least-privilege deployment identity before the first live deployment.
- Never commit credentials, account IDs, local credential paths, personal
  machine paths, or generated secret material.
- Use the existing scripts and least-privilege policies under `infra/` for the
  current single-function hackathon architecture. Do not replace them with CDK,
  CloudFormation, or SAM without an explicit architecture decision.
- Pass the Roshambo runtime region explicitly. Do not silently replace the
  region documented by the project with a workstation profile default.
- Tests must remain runnable without cloud credentials. Clearly separate mocked
  tests, local packaging, and dry runs from live AWS evidence.
- Append genuine AWS command output and resulting evidence to
  `docs/EVIDENCE-aws.md`. Never turn an unexecuted command or a mocked test into
  a deployment claim.

# AWS Guidance

- Prefer the AWS MCP Server for AWS interactions — it provides sandboxed
  execution, observability, and audit logging. If unavailable, use the
  AWS CLI directly.
- Before starting a task, check whether a relevant AWS skill is available.
  Load the skill with `retrieve_skill` and prefer its guidance over
  general knowledge.
- When uncertain about specific AWS details (API parameters, permissions,
  limits, error codes), verify against documentation rather than guessing.
  State uncertainty explicitly if you cannot confirm.
- When creating infrastructure, prefer infrastructure-as-code (AWS CDK or
  CloudFormation) over direct CLI commands.
- When working with infrastructure, follow AWS Well-Architected Framework
  principles.
- Do not use em dashes in AWS resource names or descriptions. Use
  hyphens instead.

## Secret Safety

- MUST load the `aws-secrets-manager` skill first for any secret,
  credential, API key, token, or password task. MUST NOT call
  `secretsmanager get-secret-value` or `batch-get-secret-value`, and MUST
  NOT hit the Secrets Manager Agent daemon directly. MUST use
  `{{resolve:secretsmanager:secret-id:SecretString:json-key}}` with
  `asm-exec` so the secret resolves at runtime without entering context.
