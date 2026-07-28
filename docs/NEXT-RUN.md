# The next field run: what is ready, what is not, and what a two-machine run would take

**2026-07-28 implementation update:** the host-evidence architecture described below is
now implemented locally. Stable agent keys are registry-backed; claims and audit rows
carry immutable framework/host snapshots; `register-agent` and `decide` are reachable
from the CLI; registration and heartbeat are reachable from MCP; `run_field.py` requires
`--host-label`; and host-qualified ids do not collide across machines. The offline suite
passes. **No true two-machine run was performed**, so that external acceptance gate stays
open. The original 2026-07-27 analysis below is preserved as the decision record; its
"not built" statements describe the earlier baseline, not current source.

**Original status: no run was performed.** The token budget needed to recover, and the deadline is
far enough away that waiting costs nothing. This document exists so that whoever starts
the next run — in a week or in a month — does not have to repeat the analysis.

Everything below was checked on **2026-07-27**. Two kinds of statement are kept apart:
what was *verified* against the live cluster, and what is *analysis* of a run that has not
happened. Nothing here is a measurement of a two-machine run, because there has not been
one.

---

## Part 1 — The heartbeat change is ready

`heartbeat` had existed in the library since the first release and `README` prescribed
"claim / heartbeat / release", but the verb was reachable from neither the CLI nor MCP.
Agents could not follow the pattern they were told to follow. In the field run
`starmap-2026-07-27` every lease therefore ran on its TTL alone and three lapsed mid-task.
That is fixed; this section records that the fix works.

### Verified live against the cloud cluster

The whole sequence a field agent now hits, driven end to end through the wrapper:

| Step | Result |
|---|---|
| `heartbeat` on a live lease | `RESULT=OK`, exit 0 — the lease is renewed |
| lease allowed to lapse, second agent claims | takeover granted |
| `heartbeat` on the stale id, with `--resource` | `RESULT=EXPIRED held_by=… intent=…`, exit 3 |
| `release` on the stale id, with `--resource` | `RESULT=EXPIRED held_by=…`, exit 3 |
| `release` by the real holder | `RESULT=OK`, exit 0 |
| `release` of an unknown id on a **free** resource | `RESULT=NOOP`, exit 3 |
| `--resource` reaching `roshambo.cli` | never — its parser would exit 2 |

The `NOOP` row is the important one: it is what makes `EXPIRED` mean "somebody else has
it" rather than "the release failed". Without it the new status would be a blanket alarm.

The flag was also checked in the position the prompts actually tell agents to type it —
**after** the positional argument (`heartbeat <claim_id> --resource <res>`), not before.

This is now `tests/test_demo_rsb_heartbeat_live.py` (5 tests, `-m live`), so it is
reproducible rather than a claim in a report. Offline suite 105 passed / 50 skipped,
ruff clean.

### The apparatus renders the changed prompts correctly

`run_field.py` was dry-run for both scenarios: no placeholder left unresolved, the TTL
substituted, and the heartbeat line carrying the real launcher path and `--resource`.

One small asymmetry, not a blocker: the star map prompt spells the resource out
(`starmap:task:NN`), while the fieldkit prompt says `<the resource you claimed>` and
leaves the agent to substitute. The first form is harder to get wrong.

### Prerequisites, as of 2026-07-27

Present and checked: the cluster is reachable; all three vendor CLIs are installed;
Antigravity answers (it had been quota-exhausted earlier in the week, so this is worth
re-checking before a run); the standalone interpreter that Codex's sandbox can reach is
in place.

Still needed at run time: a **fresh swarm id** and a **fresh workspace directory outside
the repository** — the previous runs' directories are still on disk — and `--interpreter`
pointed at the standalone toolchain, or Codex drops out of the run as it did in the pilot.

### TTL: measured, not guessed

`run_field.py` still defaults to 120 seconds. That was chosen when leases could not be
renewed at all, deliberately short so a dead agent would not hold a resource for long.
The heartbeat removes that reason.

Task durations from the last run, measured as the interval between being granted a task
and writing the trail for it:

| | |
|---|---|
| tasks measured | 11 |
| shortest | 16 s |
| median | 82 s |
| longest | 355 s |
| **over the 120 s TTL** | **5 of 11** |

By vendor: Antigravity 16–42 s, Codex 82 s, Claude Code 68–355 s.

**Recommended: 300 seconds** — the library's own default (`config.py`), so it needs no
separate justification, and it covers ten of the eleven measured tasks outright.

**The residual gap, stated plainly:** the interval between claiming a task and finishing
its first file cannot be covered by any heartbeat, because there is nothing to report
before then, and the prompts deliberately renew on progress rather than on a timer. The
longest such interval measured was 355 s, so even at 300 s one task in eleven would still
lapse.

That is a choice about what the run is for, not a defect:

- **If the goal is zero mid-work lapses**, use 420 s — it covers every measured case with
  margin.
- **If the goal is to test the heartbeat while keeping takeover visible**, use 300 s and
  treat an occasional lapse as a result rather than a failure. Takeover after a lapse is
  part of what the system is supposed to do; a TTL long enough to hide it also hides the
  behaviour.

---

## Part 2 — A two-machine run: analysis only

The idea is to run the agents from different machines rather than as sibling processes on
one. This section is analysis of what that would take. **Nothing was built and nothing was
started.**

### Why it is worth doing

`rsb.py` claims coordination between agents that do not know each other — "different
vendors, different machines, different sessions". Two of those three are demonstrated. The
middle one is not: `run_field.py` starts local subprocesses, so every run so far happened
on one machine, and `docs/EVIDENCE-multivendor.md` says so.

For a submission built on a distributed database that gap is the one that matters. A
single-machine run is explainable by a local file; two machines are not.

### 1. Provability — and this one touches the claim, not the mechanics

`run.json` and `evidence.json` contain no host field at all (checked: zero occurrences).
That much is easy to add. The real problem is underneath, and it is the same shape as the
heartbeat gap:

- The schema **already models this**. The `agents` table carries `framework` and `host` —
  exactly what a two-machine run would need to show.
- `Roshambo.register_agent()` **exists** in `memory.py` and inserts both.
- It is reachable from **neither the CLI nor MCP**. No agent can call it.
- Consequently the `agents` table holds **0 rows** for both field runs, while `audit_log`
  shows 9 distinct agent ids in each.
- And even registering would not close it: `agents.agent_id` is a generated `UUID`, while
  `claims.agent_id` and `audit_log.agent_id` are free-form `STRING` and commented in the
  schema as "not a FK". An audit row cannot be joined to a host.

**Why this is more than plumbing.** The project's own evidence rule is that the agents'
narration is hearsay and `audit_log` is the witness. For a two-machine run the witness is
structurally blind to the exact dimension being proven. Recording the host in `run.json`
instead would be a driver-side record — the driver's narration rather than the agents',
which is the same class of evidence the project rejects elsewhere. It would prove that the
driver *believed* it started an agent on another machine.

So a two-machine run is not worth doing until the host reaches the same table the claim is
written into. Two shapes, neither of them attempted here because both touch files that are
deliberately frozen:

- **Smaller:** an optional `host` column on `claims` and `audit_log`, filled from wherever
  `agent_id` already comes from. Follows the path that identity already travels; touches
  the schema and `leases.py`.
- **Cleaner:** make `register_agent` reachable and give claims a real reference to it.
  Fixes the identity model rather than working around it, but changes what `agent_id`
  means and therefore `CONTRACT.md`.

This is a decision for whoever owns the core, not something to slip into a demo.

### 2. The git problem

The star map protocol claims `starmap:repo` before committing, because two agents writing
one git index corrupt it. That presupposes **one** working copy. Two machines have two.

| Option | What it costs |
|---|---|
| **Shared remote; the claim guards the push instead of the index** | The resource name stops describing what it protects (it would be `starmap:push`). A claim serialises pushes but not divergent history, so agents would still need pull-then-rebase — and git's own machinery would be doing part of the coordinating, which muddies who coordinated what. |
| **Only one machine commits; the other only writes data files, collected afterwards** | The second machine's work never appears in the timeline under its own authorship — losing the multi-machine story exactly where it would be most visible. |
| **One working copy on a share over the tailnet** | Reintroduces precisely what this project argues against: file coordination across a network share, where `O_EXCL` is not atomic. It would undercut the README's own argument. |

**None of them is clean, and that is the useful finding.** Every option is really about
*who arbitrates git*, not about whether Roshambo coordinates agents. Git entered the design
as a convenient timeline, and on one machine it was free; across two it becomes a second
coordinator competing with the one under test.

**The strongest version therefore takes git off the critical path.** Let the agents write
data files and nothing else, and rebuild the timeline from what Roshambo already records —
`audit_log` timestamps and `trails`. The renderer is already data-driven, so frames can be
reconstructed from recorded state rather than from commits; one machine can collect and
commit the result afterwards as a plain archive step. Then Roshambo is the only coordinator
in the picture, and the timeline is database time rather than commit time — which is also
the more honest thing to show, since database time is what the coordination actually ran on.

The cost is real and should be named: per-agent git authorship disappears from the artifact,
and the frame reconstruction has to key off trails instead of commits. That is work, and it
is the reason this is written down rather than done.

### 3. Starting the agents on the second machine

`run_field.py` starts local subprocesses, and the smallest honest answer is that it does
not need to do anything else: **run it on both machines against the same `--swarm`**, each
with its own `--agents` subset. No remote execution, no orchestration layer. Coordination
happens in the database, which is the entire point — an orchestrator reaching across to
start the other side would be another coordinator in the picture.

Tailscale is available and both machines are on the tailnet with a direct connection, but
**the run would not need it.** Each machine needs to reach the cluster over the internet
and nothing else. That is worth stating positively: the design does not require the two
machines to be able to see each other at all, which is a stronger claim than needing a VPN.

**One concrete blocker.** Agent ids would collide. `--instances 3` produces
`claude-code-1..3` on whichever machine runs it, so two machines would mint the same ids,
and the collector strips the `-N` suffix to determine the vendor. Colliding ids would
corrupt both the attribution table and the cross-vendor count — the headline figure.
A per-host label feeding the agent id (`claude-code-lg-1`) would fix it, and the collector's
vendor lookup would need to tolerate the extra segment. Small, but it must happen before
the run, not after.

Starting both sides at the same instant is not required and not desirable: uncontrolled
arrival timing is what makes the collisions genuine. Within a minute of each other by hand
is enough.

### 4. What each machine needs against the cluster

Per machine, and nothing shared between them:

- the connection string, in a file named by `ROSHAMBO_DSN_FILE` — never passed to an agent
- the cluster's CA bundle via `ROSHAMBO_SSLROOTCERT_FILE`; on Windows the bundled OpenSSL
  has no system trust store, so `verify-full` fails without it (`docs/EVIDENCE-cloud.md`)
- a Python interpreter the vendor sandboxes can actually reach — Codex's could see nothing
  under the Windows user profile, which cost the pilot run its third vendor
- the vendor CLIs installed and authenticated
- the same `--swarm` and the same `--ttl` on both sides

No shared filesystem, no shared git remote, no open ports between the machines.

---

## What is open

- **No two-machine run has been performed.** The host-evidence and agent-id prerequisites
  are implemented, but the result itself must still be produced on two real machines.
- Keep git outside that future run's coordination-critical path; collect and archive the
  result after the database-backed run.
- Task 12 of the star map (the legend) was never claimed, so the renderer's third optional
  hook has still only been exercised by its tests.
