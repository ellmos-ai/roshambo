# Evidence: coordination between agents from three different vendors

What this document is for: the demo application's collision scene proves that the
mechanism works, but its three racers are three threads in one process. Roshambo's
positioning claims something stronger — coordination between agents that do not know
each other, across vendors, processes and sessions. Nothing technical was missing (a
claim is an ordinary serializable transaction and the database does not care who calls
it), but demonstrated and asserted are not the same thing. This is the demonstration.

The counting rules were registered in `demo/multivendor/PROTOCOL.md` **before any agent
was started**, and committed first, so the figures below could not be shaped by looking
at the data. Everything is read from the `audit_log` table, not from the agents' own
reports: two of the three are third-party runtimes whose narration deserves no more
trust than anyone else's, and an agent can misread its own result.

---

## Two use cases, deliberately kept apart

Roshambo does both of these, and the second is the one no vendor's own tooling offers.

**1. A team whose members know about each other.** The demo application's four-beat
script (`docs/EVIDENCE-demo.md`) runs three agent runtimes inside one process. This is
the **reproducible acceptance test**: anyone can run it with nothing but a database
connection — no model API keys, no vendor accounts, no credentials of any kind. It
remains the way to check that Roshambo does what it says.

**2. Agents that have never met.** Three coding agents from three different vendors,
each in its own operating-system process and its own fresh session, sharing nothing but
the database. This is **demonstrated here rather than offered as a test you must run**,
because reproducing it requires accounts with three separate model vendors. We are not
pretending a reader can repeat it on a whim; the apparatus is in the repository if they
want to.

Neither replaces the other. The first is what you can check; the second is what the
product is for.

---

## Participants

| Agent id | Vendor | Runtime | Model |
|---|---|---|---|
| `claude-code` | Anthropic | Claude Code CLI, headless | default |
| `codex` | OpenAI | Codex CLI via the companion script | default, low effort |
| `agy` | Google | Antigravity CLI | `gemini-3.6-flash`, low effort |

Every invocation is a new process with a new session. The agents share no memory, no
message bus, no in-process task list and no file handle. Everything one knows about
another, it learned from Roshambo.

## The access path: a command, not MCP

All three vendors reach Roshambo through `demo/multivendor/rsb.py`, a thin wrapper over
the existing CLI. The choice was made on the following grounds and is recorded here
because it is itself an argument for the product:

Running a command and reading its output is the one calling convention every coding
agent already has. Teaching three vendors to speak Roshambo's MCP server would have
meant editing three vendor configuration files — which is exactly the per-vendor special
case Roshambo claims to remove. Proving the claim that way would have undercut it.

MCP is not re-proven here. It is already the demonstrated native path for clients that
speak it (`docs/EVIDENCE-iface.md`, phase 3.5).

The connection string never reaches an agent. Third-party runtimes transmit their
context to their vendor, so the DSN is resolved inside the wrapper process from a file
path; the driver strips every `ROSHAMBO_*` variable from the agents' environment.

**Gap, stated rather than filled:** `decide` exists as an MCP tool but not as a CLI
subcommand, so it was not exercised. It is not part of the collision proof, and adding
it would have been a change of direction rather than a measurement.

## What the agents were doing

They jointly built **roshambo-fieldkit**, twelve small independent pure functions, one
per task, each with its own tests. All three received the *same ordered task list* and
the same instruction: take the first task that is not yet done. Three fresh sessions
reading the same ordered list genuinely reach for the same task — that is the failure
mode Roshambo exists to prevent, not a staged race. Whether two of them overlapped in
time was decided by the operating system's scheduling of independent processes.

Two classes of shared resource, **counted separately and never summed**:

- `fieldkit:task:NN` — one per task. A collision here means two agents independently
  wanted the same work. This is the figure that matters.
- `fieldkit:index` — one registry file every agent must hold briefly to record a
  finished task. A deliberately created serialization point, so collisions here are
  expected by construction and are reported apart.

---

## The pilot run, which found nothing — and why that matters

Before the run that produced the numbers below, a pilot ran **one** invocation of each
vendor. It produced **zero collisions**, and it is reported here rather than quietly
discarded, because it is the best evidence that the later collisions were not
manufactured.

The audit log gives the reason precisely. `agy` claimed task 01 at t=0 and released it
at t=25s. `claude-code` did not claim anything until t=101s. `codex` never reached
Roshambo at all. Arrivals were roughly 100 seconds apart while leases were held for 25
to 125 seconds, so no two agents were ever inside the same window. Nothing was wrong
with the coordination layer; there was simply no contention for it to resolve.

The remedy was more concurrent arrivals — `--instances`, which starts several
invocations of each vendor per round. The alternative remedy, widening the definition of
a collision, was ruled out in advance by `PROTOCOL.md`.

The pilot also surfaced an honest limitation of the design, kept rather than patched:
**an agent was granted a lease on a task that was already finished.** A lease says
nobody is working on this; it does not say the work still needs doing. Both checks are
necessary, and the agent protocol now asks for both. It was noticed only because the
agent's file-writing tool refused to overwrite an existing file — a second line of
defence catching what the first had let through.

---

## Vendor findings

Three things were measured that anyone attempting this will hit, and none of them are
about Roshambo:

**Exit codes do not survive three vendor shells.** Asked to run a script that exits 3
and report the code, the Antigravity agent reported 1. The script demonstrably exits 3.
Each vendor drives a different shell, and a command that cannot be found also exits
non-zero, so "claim refused" and "your shell could not find the wrapper" are the same
signal to the agent. The wrapper therefore answers on stdout, with
`ROSHAMBO RESULT=GRANTED|DENIED|OK|NOOP|ERROR` as its first line. Exit codes are still
set, but they are no longer the only channel.

**A sandbox decides which doors an agent can open.** Codex's sandbox could reach nothing
under the Windows user profile: first the virtualenv shim failed to reach its base
interpreter, then the base interpreter itself came back as "not recognized as an
executable program". Both reproduced in isolation. This silently cost the pilot run its
third vendor. The fix was to place a standalone interpreter where every participant
could read it. It is an argument for the front door being a plain command rather than
anything heavier: the more infrastructure the door needs, the more sandboxes will refuse
it.

**The vendors' own tooling has concurrency limits that Roshambo does not.** Running
three Codex companion instances at once fails: they collide on Codex's own SQLite state
store under the user's `.codex` directory, and the loser exits with
`failed to initialize sqlite state runtime`. The shared coordination layer they were all
talking to handled the same concurrency without complaint.
