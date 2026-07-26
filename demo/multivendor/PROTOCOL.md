# Multi-vendor field run — protocol

**Registered before the run, not after.** The counting rules below were written and
committed before any agent was started, so the numbers in
`docs/EVIDENCE-multivendor.md` cannot have been shaped by looking at the data first.
The commit that adds this file precedes the commit that adds the results.

---

## 1. What is being tested

Roshambo's positioning claim is that it coordinates agents **that do not know each
other** — different vendors, different processes, different sessions — as opposed to
Claude Code Agent Teams (one vendor, one session) or Bedrock Multi-Agent Collaboration
(one vendor, one account).

The demo app's collision scene proves the *mechanism* but not the *claim*: its three
racers are three threads in one process. Nothing technical is missing — a claim is an
ordinary serializable transaction and the database does not care who calls it — but for
a reader the difference between *demonstrated* and *asserted* is exactly this.

This run closes that gap. Two things are being established:

1. Three agents from three different vendors, each in its own OS process and its own
   fresh session, coordinate through Roshambo and nothing else.
2. They do so while actually building something, over a long enough period that
   collisions arise on their own. No synchronised starting gun.

## 2. Participants

| Agent id | Vendor | Runtime | Invocation |
|---|---|---|---|
| `claude-code` | Anthropic | Claude Code CLI | `claude -p` |
| `codex` | OpenAI | Codex CLI via the companion script | `codex-companion.mjs task` |
| `agy` | Google | Antigravity CLI | `agy.exe --model gemini-3.6-flash --effort low -p` |

Each round starts a **new process with a new session**. The agents share no memory, no
task list in RAM, no message bus, and no file handle. Everything they know about each
other, they learn from Roshambo.

## 3. Access path: the shell, not MCP

All three vendors are reached through `rsb.py`, a thin wrapper around the existing
`roshambo` CLI. The reasoning is recorded in that file's docstring; in short: "run a
command, read the exit code" is the one calling convention every coding agent already
has, whereas teaching three vendors to speak our MCP server would require editing three
vendor configuration files — the per-vendor special case the product claims to remove.

MCP is not being re-proven here. It is already demonstrated as the native path for
clients that speak it (Phase 3.5, `docs/EVIDENCE-iface.md`).

The wrapper also keeps the DSN out of the agents' context. Third-party agent runtimes
transmit their context to their vendor, so the connection string is resolved inside the
wrapper process from a file path, never passed to the agent.

**Known gap:** `decide` exists as an MCP tool but not as a CLI subcommand, so it is not
exercised in this run. It is not part of the collision proof. Adding it would be a
scope change and was not made.

## 4. The shared work

The agents jointly build **roshambo-fieldkit**, a small collection of independent pure
Python helper functions, one per task, each with its own tests. The work is real and the
result is inspectable: at the end the whole thing is run under `pytest` and the result is
reported as measured, pass or fail.

Contention is *not* manufactured. All three agents are handed the **same ordered task
list** and told to take the first task that is not yet done. Three fresh sessions reading
the same ordered list will genuinely reach for the same task — that is the precise
failure mode Roshambo exists to prevent, not a staged race. Whether two agents overlap in
time is decided by the operating system's scheduling of three independent processes, not
by us.

Two kinds of shared resource, **counted and reported separately**:

- `fieldkit:task:<NN>` — one per task. A collision here means two agents independently
  wanted the same piece of work. **This is the number that matters.**
- `fieldkit:index` — a single registry file every agent must claim briefly to record a
  finished task. This is a deliberately created serialization point. Collisions here are
  real but *expected by construction*, so they are reported as a separate figure and
  never added to the first.

## 5. Counting rules (registered in advance)

All figures come from the `audit_log` table in CockroachDB, not from the agents' stdout.
The agents' own reports are hearsay: an agent can misread its own exit code, or claim
success it did not have. The audit log is written by the code path that actually made the
decision.

Lease TTL for this run: **120 seconds**, set explicitly on every claim.

Definitions:

- **Grant** — an `audit_log` row with `verb='claim'`, `allowed=true`, resource `R`,
  agent `A`, timestamp `t_g`. Its **lease window** is `[t_g, t_g + 120s]`.
- **Denial** — an `audit_log` row with `verb='claim'`, `allowed=false`, resource `R`,
  timestamp `t_d`, `reason='held by X'`.
- **Genuine collision** — a denial on `R` at `t_d` for which the most recent preceding
  grant on `R` is by agent `X` at `t_g`, **and** `t_d - t_g <= 120s`, **and** the holder
  named in the denial's reason is exactly that `X`.
  A denial that names a holder whose lease had already lapsed is *not* counted; a denial
  that names a *different* holder than the live grant is not a collision but a **defect**
  (it would mean two live leases) and is reported as such.
- **Distinct contention event** — one `(grant, denying agent)` pair. An agent that
  retries the same blocked resource five times produces five denials but **one** contention
  event. Both numbers are reported; the contention-event count is the headline, because
  the denial count can be inflated by retry behaviour.
- **Cross-vendor collision** — a genuine collision where the denied agent and the holding
  agent are from different vendors. **This is the figure that carries the claim.** A
  collision between two invocations of the same vendor proves concurrency but not
  heterogeneity.
- **Informative denial** — a genuine collision whose reason names the holder. Since the
  holder's identity is what lets a blocked agent do something useful instead of waiting,
  the share of informative denials is reported. (Note: an agent *querying* `who-has` leaves
  no audit row — `who_has` is not audited — so only the denial path is provable here.)

## 6. What would make this run fail

If the run produces **no genuine collision on a task resource**, then the claim is not
demonstrated and the run is reported as inconclusive. It will not be re-described as a
success, and the counting rules above will not be widened after the fact to make a number
appear. The honest remedies, if it comes to that, are more concurrent invocations and
shorter tasks — not a redefinition of "collision".

## 7. Reproducing it

The apparatus is in this directory. The run workspace is created outside the repository
(unattended third-party agents get a writable root that is not this repo) and is not
committed. See `README.md` here for the exact commands.
