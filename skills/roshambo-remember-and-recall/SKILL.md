---
name: roshambo-remember-and-recall
description: Use Roshambo's shared, persistent memory correctly. Call recall() before starting any task that is not obviously routine, so you find out whether it has already been tried and how it ended, failures included. Call remember() once you have an outcome, even (especially) a failure. Use when working in a Roshambo-backed agent swarm, when a task feels unfamiliar, when you are about to repeat what another agent might already have attempted, or when you have just finished an attempt and its outcome is not yet recorded.
---

# Remember and recall with Roshambo

Roshambo is a multi-agent coordinator on CockroachDB; alongside coordination, its
second characteristic is **negative memory**: it stores the outcome of attempts,
failures included, and makes it possible to find a prior attempt again later, even when
a new query is worded differently than the original entry. A human remembers their own
dead ends. A freshly spawned agent does not — unless a previous agent wrote it down, and
this one reads it before acting.

This is vector search over embeddings. As of the last verified run, retrieval has been
demonstrated to work across reworded queries using a lexical (word- and
character-trigram-based) embedder — a real semantic embedding path (Amazon Titan via
Bedrock) is implemented but not yet verified in that same run. Treat `recall()` as "will
find a prior entry even if you word it differently," not yet as "understands what you
mean" — see this repository's `docs/EVIDENCE-core.md` for the exact, current state.

Access Roshambo through the `roshambo-mcp` MCP server's tools: `recall` and `remember`
(this skill), and `claim` / `release` / `decide` / `status` (see the sibling skill
`roshambo-claim-work` for the lease side).

## The one habit this skill teaches: recall before you act

**Before starting any task that is not trivially routine, call `recall()` first.**

```
recall(query="<a plain description of what you are about to try>", limit=5)
```

- `query` does not need to match anyone's exact prior wording. `recall()` is a vector
  search over `topic` + `approach` + `evidence`, so a differently-phrased description
  of the same underlying attempt can still surface it.
- Read every hit's `outcome`, not just its `topic`. A hit with `outcome: "failure"` or
  `"abandoned"` is the single most useful thing recall can return: it tells you what
  *not* to repeat, and the `evidence` field tells you why. Treat it as a warning worth
  reading in full, not as noise to skip past on the way to a `"success"` hit.
- Optionally narrow with `outcomes=["failure", "abandoned"]` when you specifically want
  to search for prior dead ends before choosing an approach.
- If nothing relevant comes back, that is useful too — it means you are not repeating
  known work, not that recall "didn't work".
- `recall()` never modifies anything. Call it liberally; there is no cost to checking
  first.

## The other half: write a trail once you have an outcome

**Call `remember()` as soon as you have a definite outcome for something you tried —
not just at the end of a whole task, but for each attempt worth recording.**

```
remember(
    topic="<what this attempt was about, short>",
    approach="<what you actually tried>",
    outcome="success" | "failure" | "abandoned" | "inconclusive",
    evidence="<concrete: an error message, a measurement, a quote — whatever justifies
               the outcome to a reader with no other context>",
)
```

- **Write failures and abandoned attempts exactly like successes.** This is the entire
  point of the memory: a failure trail is what lets the next agent — possibly you, in a
  fresh session with no context — skip a dead end instead of re-discovering it the hard
  way. Do not skip `remember()` just because an attempt didn't work.
- `evidence` should be concrete enough to stand alone: "timed out after 30s on a
  50k-row dataset" is useful; "didn't work" is not. A later agent's `recall()` query is
  likely to match on the specifics in `evidence`, not on a vague summary.
- If the output is large (long logs, a full transcript), store it externally (for
  example via the AWS side's S3 artifact storage) and pass its reference as
  `artifact_uri` instead of inlining everything into `evidence`.
- `outcome` must be exactly one of `success`, `failure`, `abandoned`, `inconclusive` —
  no other values are accepted.

## Why this matters more than it looks like it does

Skipping `recall()` does not fail loudly. It just means you silently redo work someone
else (or a past version of you) already did and already learned something from. That
failure mode is invisible until someone notices duplicated effort or a repeated dead
end across a multi-agent run — by which point the wasted work is already spent. Treat
`recall()` before, `remember()` after as the default shape of any non-trivial task in a
Roshambo-backed swarm, not an optional extra step.

## Related

- `roshambo-claim-work` — lease discipline (`claim` / `heartbeat` / `release`) for
  resources more than one agent might attempt at once.
- `docs/mcp-managed.md` in this repository — the separate, read-only path for schema
  introspection and ad-hoc SQL analysis; `roshambo-mcp` itself has no SQL tool.
