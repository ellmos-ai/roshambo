# A sky invented by three vendors

A **record**, not a library. Kept exactly as the agents wrote it, which is why it is
excluded from this project's linting and formatting (`pyproject.toml`,
`tool.ruff.extend-exclude`) — reformatting it would falsify the record.

Ten constellations and two rendering modules, built on 2026-07-27 by coding agents from
Anthropic, OpenAI and Google, each a separate process in a separate session, coordinating
through nothing but a CockroachDB cluster.

**The sky is invented.** Nothing here claims to be astronomy. Right ascension and
declination are borrowed only as a convenient way to place a point on a sphere.

- `starmap.svg` — the finished map.
- `frames/` — the same map rebuilt at each of the eight commits, with `frames.json`
  naming the commit, author and timestamp behind every frame.
- `data/constellations/` — what the agents wrote.
- `starmap/` — the two modules they contributed to the rendering itself.
- `run.json`, `evidence.json` — process timings, and the full output of applying the
  pre-registered rules to the `audit_log`.

## Who built what

From the granted `claim` rows in `audit_log`, not from the agents' own reports. Where a
task was granted twice, both holders are named and the reason is in the findings below.

| Task | What it is | Won by | Vendor |
|---|---|---|---|
| 01 | The Salt Hook / The Silver Weasel | `claude-code-2`, then `agy-3` | Anthropic, then Google |
| 02 | The Cracked Bell | `claude-code-3`, then `claude-code-1` | Anthropic |
| 03 | The Glass Kite | `codex-1` | OpenAI |
| **04** | **the projection — mathematics** | **`codex-3`**, then `agy-3` | **OpenAI**, then Google |
| 05 | The Weaver's Shuttle | `agy-2` | Google |
| 06 | The Obsidian Mirror | `agy-1` | Google |
| 07 | The Sunken Harp | `claude-code-1` | Anthropic |
| **08** | **the palette — graphic design** | **`claude-code-3`** | **Anthropic** |
| 09 | The Ferryman's Oar | `claude-code-2` | Anthropic |
| 10 | The Bone Ladder | `claude-code-1` | Anthropic |
| 11 | The Silver Compass | `agy-2` | Google |
| 12 | the legend — structure | *nobody* | — |

Nobody was assigned a task. All twelve sat in one ordered list and anyone could claim
any of them, so this split is what the leases handed out.

**One result in each direction, and both are reported.** The mathematics task went to
OpenAI's agent, which is the vendor whose documented strength is formal accuracy — earned,
not arranged. The graphic-design task went to Anthropic's agent rather than to Google's,
whose documented strength it is. A single run cannot show that a capability-based split is
optimal, and this one does not claim to.

Task 12, the legend, was never claimed. The run ended with tasks still on the list.

## What the specialists actually produced

**`projection.py`** is a correct stereographic projection: centred on the north celestial
pole, guarded against the division that blows up at the pole, and normalised so the
southern visible limit meets the shorter canvas edge. Our first reading blamed it for a
crowded-looking map; that was wrong, and the crowding was only that part of the sky was
still empty at the time.

**`style.py`** contains actual design reasoning rather than a lookup table. Its own
comments explain that the spectral colours are pushed past true blackbody values because
"at a radius of two pixels on a dark ground an honest colour is simply not visible", and
that F and G are kept near white to keep the progression monotone "rather than rainbow".
Every function is written to be total, because the renderer's silent fallback would
otherwise be indistinguishable from a bad palette.

## Findings

**All nine bands were respected.** Every constellation stays inside the right ascension
band its task assigned, keeps declination within ±60°, and carries six to eight stars as
specified. The renderer skipped none of them and nothing had to be repaired.

**Three tasks were granted twice, and the reason is the lease duration, not the design.**
Tasks 01, 02 and 04 each went to a second holder after the first. One agent recorded
exactly why, in a `failure` trail it wrote itself:

> my 120s task lease expired mid-run and `starmap:task:02` was re-claimed by
> `claude-code-1` afterwards, so a competing `02-*.json` may appear

The work simply took longer than the lease it was granted under. That is a configuration
finding — the TTL was set shorter than the task — not a fault in the coordination, and it
is the honest explanation for the duplicate constellations left in `data/`.

**A commit is coarser than a claim.** `starmap:repo` was claimed eleven times but produced
only eight commits, because each agent ran `git add -A` and so swept up whatever other
agents had finished but not yet committed. The lease serialised access to the git index
correctly; what did not match was the granularity — the claim covered the repository, the
commit covered the whole tree.

**The protocol held under refusal.** The same agent that lost its lease also lost
`starmap:repo` five times in a row, to four different agents across three vendors. It did
what it was told: left its file uncommitted, recorded a `failure` trail explaining the
situation, and stopped — rather than forcing the commit. Its file was picked up and
committed by a later agent.
