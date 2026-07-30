# Evidence — Bedrock (Titan Text Embeddings V2)

Per CONTRACT.md ground rule 2 ("no overclaiming"): what ran for real is recorded with
its exact output; what did not run is marked as not run, and why. This file is
specifically about the real Bedrock path — `docs/EVIDENCE-aws.md`/`EVIDENCE-cloud.md`
cover the rest of the AWS integration and record this same test skipping for the
unrelated, earlier reason of no credentials being configured at all; this file covers
what happened once credentials, region routing, and IAM were all correct.

## What is verified

One real `InvokeModel` call against `amazon.titan-embed-text-v2:0` in `us-east-2`, via
SigV4 (AWS profile `ellmos-ai`), succeeded and returned a genuine 1024-dimensional
embedding vector:

| | |
|---|---|
| Date/time | 2026-07-30, ~22:38 local (CEST) |
| Call | `bedrock-runtime InvokeModel`, model `amazon.titan-embed-text-v2:0`, region `us-east-2` |
| Input text | `"roshambo smoke test"` |
| Response | `inputTextTokenCount: 7`; `embedding` array length **1024**; first five values `-0.03554, 0.00071, 0.03754, -0.03498, 0.06408` |
| Raw response | [`docs/evidence-artifacts/bedrock-titan-embed-v2-us-east-2-2026-07-30.json`](evidence-artifacts/bedrock-titan-embed-v2-us-east-2-2026-07-30.json) (43,282 bytes) — array length and the values above re-verified programmatically against this file before writing this document, not copied from a report unread |

This proves the request shape, SigV4 authentication, and region routing this project's
code actually uses (`roshambo.embeddings.BedrockEmbedder`, reading `cfg.bedrock_region`)
are all correct against the real service — not mocked, not assumed.

## What is NOT verified

`tests/test_core_recall.py::test_recall_with_the_real_embedder` — the test that would
show `recall()` finding a rephrased prior failure via genuine semantic embeddings, not
lexical overlap — has never completed successfully. Every attempt after the one
successful call above was skipped:

* 3 attempts in this session (2026-07-30, spanning several minutes, `AWS_PROFILE=ellmos-ai`,
  `AWS_BEARER_TOKEN_BEDROCK` confirmed unset): all `SKIPPED`, all with the identical cause:
  ```
  SKIPPED [1] tests/test_core_recall.py:345: embedder is not usable here:
  ThrottlingException: An error occurred (ThrottlingException) when calling the
  InvokeModel operation (reached max retries: 4): Too many requests, please wait
  before trying again.
  ```
* Further attempts reported separately by the operator, re-checking after their own
  successful call: also throttled, same exception.

Consequently: the "recall finds a prior entry via different wording" claim that **is**
backed by evidence (`docs/EVIDENCE-core.md`, `docs/EVIDENCE-cloud.md`) rests entirely on
the offline lexical placeholder embedder (word tokens + character trigrams), not on
Bedrock. No semantic-retrieval claim is supported by any evidence in this repository.

## Why: quota, not credentials, not code

Checked directly against the AWS Service Quotas API, not inferred from the throttling
behaviour alone:

```
$ aws service-quotas list-service-quotas --service-code bedrock --region us-east-2 \
    --query "Quotas[?contains(QuotaName, 'Titan Text Embeddings')].{Name:QuotaName,Value:Value,Adjustable:Adjustable}"
```

| Quota (Titan Text Embeddings V2, quota code `L-26C560CE`) | Value | Adjustable |
|---|---|---|
| On-demand model inference requests per minute | 0.0 | false |
| On-demand model inference tokens per minute | 0.0 | false |

Re-checked by the operator after their own successful call and found at `0.0` in
**both** `us-east-2` and `eu-central-1`. The explanation consistent with every
observation collected: this AWS account's Bedrock model-access grant came with a small,
one-time burst allowance that the single successful call above consumed, not a
sustained quota. AWS's documented behaviour for freshly-granted, low-usage accounts is a
conservative ramp-up that loosens with usage history; whether waiting longer (hours or
days) restores capacity was not tested here — out of scope for a fixed hackathon
submission window.

Because the failure mode is `ThrottlingException` — not `AccessDeniedException`,
`UnauthorizedException`, or `NoCredentialsError` — authentication, IAM permissions, and
region routing are all confirmed correct: the request reaches the model and is
recognized as authorized, then rejected purely on rate.

## Corrected in this document

An earlier claim in `README.md`/`README_de.md`/`demo/README.md` (added the same session
as the Bedrock/AWS region split, before the quota was re-measured) stated a `us-east-2`
on-demand quota of "6000". That number came from a single successful call at the time of
the initial model-access grant, not from a Service Quotas reading, and does not
reproduce: the quota reads `0.0` on every check made since, by two different people. The
three README files have been walked back to the state this document actually supports.

## What this means for the submission

* The Bedrock **integration code** is proven correct against the real service: one
  genuine round trip, real credentials, real region, real 1024-dim vector.
* The Bedrock **semantic retrieval claim** is not proven. `recall()`'s only evidenced
  retrieval quality anywhere in this repository is lexical (word/trigram overlap via the
  offline placeholder) — itself a real, tested, and honestly-described result, not a
  fallback story to apologize for.
* `ROSHAMBO_EMBEDDING_PROVIDER` stays `placeholder` on the deployed `roshambo-demo`
  Lambda until this test can actually complete against the real embedder.
