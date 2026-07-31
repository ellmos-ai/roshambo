# Multi-vendor field run

Three coding agents from three different vendors, each in its own process and its own
fresh session, building one thing together — coordinating through nothing but Roshambo.

- `PROTOCOL.md` — what is being tested and, registered in advance, how collisions are
  counted. Read this first; it is what makes the numbers in
  `../../docs/EVIDENCE-multivendor.md` worth anything.
- `rsb.py` — the front door the agents call.
- `run_field.py` — starts the agents.
- `collect_evidence.py` — reads the result out of `audit_log`.

Two joint projects have been run through it, selected with `--scenario`:

| Scenario | What the agents build | Why it exists |
|---|---|---|
| `fieldkit` | twelve small text modules with tests (`TASKS.md`, `prompts/agent.md`) | the first collision measurement |
| `starmap` | a night sky, rendered (`starmap/`, `prompts/starmap-agent.md`) | something that can be watched, and re-rendered at any commit |

### The star map, and why it is data plus a renderer

`starmap/render.py` is ours; the agents only add data files and three optional modules it
picks up if they import. That split buys the property the whole thing rests on: **every
past state can be rebuilt exactly.** `starmap/timelapse.py` walks the git history in a
throwaway clone, re-renders each commit and writes numbered frames plus a manifest, so
the sequence is evidence rather than an edit — each frame names the commit it came from,
and the renderer is deterministic, so anyone can reproduce it.

The renderer never fails. Truncated JSON, a line pointing at a star that was never
defined, a module that imports and then throws: each is skipped, counted and written into
the SVG itself. That is not defensive habit — a renderer that raised on a half-finished
commit would put holes in the time-lapse exactly at the interesting moments.
`tests/test_starmap_render.py` holds both properties in place.

The sky is invented. Nothing in it claims to be astronomy, so no coordinate is asserted
as a fact.

Three of the twelve tasks are cut along capability lines rather than by vendor — the
stereographic projection is mathematics, the palette and magnitude curve are graphic
design, the title block and legend are structure and wording. They sit in the same
ordered list as the nine constellation tasks, claimable by anyone, so who ends up doing
which is earned rather than assigned.

In this scenario the workspace is a git repository, and `starmap:repo` has to be claimed
before committing — two agents writing the index at once corrupt it. An agent refused
that claim is told to leave its files uncommitted and report, not to force it.

## Why a command and not MCP

Every coding agent can run a command and read what it prints. Only some speak MCP, and
wiring three vendors to our MCP server would mean editing three vendor configuration
files — which is exactly the per-vendor special case Roshambo exists to remove. Proving
the claim that way would have undercut it.

MCP is not being re-proven here; it is already the demonstrated native path for clients
that speak it (`../../docs/EVIDENCE-iface.md`).

## The front door

    rsb.cmd <verb> [arguments]

The first line of output is always
`ROSHAMBO RESULT=REGISTERED|GRANTED|DENIED|OK|NOOP|EXPIRED|ERROR ...`, followed by JSON. Exit
codes are still set (0 ok, 3 refused, 1 error) but are not the protocol — see the
docstring in `rsb.py` for the measurement that forced that decision.

`EXPIRED` separates the two things `NOOP` used to say at once. A `release` or
`heartbeat` that fails can mean "already handed back, nothing to do" or "your lease
lapsed and the work is somebody else's now" — and in the `starmap-2026-07-27` field
run two agents read the second as the first and committed work that had been
re-granted. Pass `--resource <resource>` alongside the claim id and the wrapper looks
up the current holder: a resource that is genuinely free still answers `NOOP`, one
that has changed hands answers `EXPIRED held_by=… expires_at=… intent=…`.
`--resource` is consumed by the wrapper and never reaches `roshambo.cli`; it is
needed because a takeover regenerates `claim_id`, so the old id identifies nothing.

The connection string never reaches an agent. `run_field.py` strips every `ROSHAMBO_*`
variable from the child environment and writes `rsb.cmd` into the workspace, which sets
them for the wrapper process alone.

## Running it

Two things have to be true before you start, and both were learned the hard way:

**The workspace must be outside this repository.** Unattended agents get a writable
root, and for Codex that root is the enclosing git repository unless told otherwise.
`run_field.py` refuses to run inside the repo.

**Every participant must be able to reach the interpreter.** Codex's sandbox could not
see anything under the Windows user profile, which silently cost an entire pilot run its
third vendor. If your interpreter lives somewhere a vendor sandbox cannot read, put a
standalone one where it can and pass `--interpreter`. On the machine this was developed
on:

    uv python install --install-dir <toolchain-dir> 3.12
    uv venv --python <toolchain-dir>/cpython-*/python.exe <toolchain-dir>/venv
    uv pip install --python <toolchain-dir>/venv/Scripts/python.exe "psycopg[binary]"

Then set the environment and go:

    ROSHAMBO_DSN_FILE                file holding the cluster connection string
    ROSHAMBO_SSLROOTCERT_FILE        CA bundle (needed on Windows for verify-full)
    ROSHAMBO_FIELD_CLAUDE_BIN        default "claude"
    ROSHAMBO_FIELD_AGY_BIN           default "agy"
    ROSHAMBO_FIELD_CODEX_COMPANION   path to the Codex companion script

    python run_field.py \
        --workspace <somewhere outside this repo> \
        --swarm <a fresh swarm id> \
        --host-label <stable-public-host-label> \
        --scenario starmap \
        --rounds 2 --instances 3 --ttl 300 --timeout 900 \
        --interpreter <toolchain-dir>/venv/Scripts/python.exe

Then read the result out of the database, not out of the agents' logs:

    python collect_evidence.py --swarm <the same swarm id> --ttl 300

And, for the star map, turn the run's own history into frames:

    python starmap/timelapse.py --workspace <the same workspace> --out <frames dir>

Use a **fresh swarm id** per run. `swarm_id` is the leading key column on every table,
so a new id gives a clean slate without deleting anything.

Use a different stable `--host-label` on every machine. The runner appends it to every
agent id, and the first command in each prompt registers that id with its framework and
host. Claims and audit events retain immutable snapshots; `collect_evidence.py` therefore
reports cross-host collisions/events from the grant and denial database rows, not from
an agent log or driver assertion. A raw count of two host labels alone is not acceptance.

## What `--instances` is for

It runs N invocations of each vendor per round. This is not a knob for making the
numbers look better — it is the answer to a measured problem. The first pilot ran one
of each: arrivals were about 100 seconds apart while leases were held for 25 to 125
seconds, so no two agents were ever inside the same window and there were no collisions
at all. Nothing was wrong with the coordination; there simply was no contention to
resolve. More concurrent arrivals is the honest remedy. Loosening the definition of a
collision is not, and `PROTOCOL.md` was committed before any of this to make that
impossible after the fact.

## BotAgent -- a protocol-conformance simulator, and the shipped stress test

`bot_agent.py` is a fourth kind of participant: not a vendor CLI, not driven by any
language model, just a small deterministic Python loop that speaks the exact same
protocol as everyone else -- register, claim, hold briefly, release -- through
`roshambo.memory.Roshambo`, never a shortcut around it. That is the point: Roshambo
coordinates whoever speaks the protocol, not a fixed list of vendors, and this
script is the proof you don't need an LLM key to check that.

### The 30-second proof anyone can run

```
python demo/multivendor/bot_agent.py --dry-run --bots 3
```

Three `BotAgent A/B/C` instances register under their own identity and claim
against *each other only*, on a fresh auto-generated swarm (never the real field
run's), for a few seconds. No files, no project, no other participants -- a pure
coordination check. It prints a summary and exits non-zero if it ever found two
simultaneous holders of the same task (expected: never). What you need is any
CockroachDB cluster (a free CockroachDB Cloud tier is enough) via `ROSHAMBO_DSN` —
but no LLM key and no vendor credentials of any kind. If you are evaluating this
submission and want to see the core guarantee hold without waiting for a field run,
this is the command.

### Storm mode: extra contention alongside a real run

Drop `--dry-run` and pass the field run's own `--swarm` to add bot-driven load
*while* the real agents are actually building something -- useful for raising
collision density (video material) or stress-testing a swarm under more realistic
concurrency than a handful of LLM sessions produce on their own:

```
python demo/multivendor/bot_agent.py --swarm <same swarm as run_field.py> --bots 3 \
  --tasks starmap:task:01,starmap:task:02
```

### Adaptive rate, and why the interval log is worth keeping

Each bot's claim interval backs off (multiplies up) on a denial and gets bolder
(multiplies down) on a grant, clamped to `--min-interval`/`--max-interval`. This is
not decoration: it makes the bot self-limiting under contention (kinder to the
cluster's Request Unit budget than a fixed-rate loop), makes it behave like a
plausible competitor instead of a blunt hammer, and is fair -- a bot on a losing
streak backs off rather than crowding a resource forever. `--log-interval-csv
<path>` writes every (timestamp, interval, outcome) row; that curve -- the rate
visibly breathing with contention -- is what the project's demo video uses.

### Reading the output

The final summary (stderr) reports, per bot and in total: attempts, granted,
denied, errored, and the final interval each bot settled at. The one line that
actually matters is `two simultaneous holders of the same task, at any point:` --
`no` is the expected, passing result; `YES -- CONTRACT VIOLATION` names the
resource and the two claim ids and means the core guarantee did not hold. Pass
`--json` for the same report as structured data (also on stdout), suitable for
copying into an evidence file.

A won claim is always released -- via `try`/`finally`, including when the
simulated hold is interrupted (Ctrl-C) or raises -- so a bot never starves the
swarm it is supposed to be stress-testing.

### Request Unit cost -- read this before raising the defaults

Every attempt is at least a claim write plus an audit-log write; a grant adds a
release write plus its own audit write. `--max-attempts-total` and `--time-limit`
are hard ceilings, on by default (conservative: a few hundred attempts, well under
two minutes), and a plan that would exceed either is refused unless you pass
`--i-have-checked-the-ru-budget`. The RU number the script prints is a labelled
*planning estimate* (see `estimate_ru_cost` in the source) -- not a measurement.
CockroachDB Basic bills Request Units against a monthly cap; check the cluster's
own console before turning `--bots`, `--max-attempts-per-bot`, or `--time-limit`
up substantially, especially against a shared or production cluster.

## Vendor quirks worth knowing

- **Antigravity (`agy`)** requires `--effort` on its current models and rejects the
  older model names outright. `--add-dir` is what grants write scope; the permission
  flag only stops it asking.
- **Codex** needs `--write` to write at all and `-C` to keep its writable root off the
  enclosing git repository. Its sandbox is the most restrictive of the three about
  paths outside the workspace.
- **Claude Code** is run with an explicit tool allowlist, so it cannot reach the web or
  spawn further agents during the run.
