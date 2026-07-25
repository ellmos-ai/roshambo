---
name: roshambo-claim-work
description: Coordinate safely in a Roshambo-backed multi-agent swarm by claiming a resource before working on it, keeping the claim alive with heartbeats during long work, and releasing it promptly when done or abandoned. Use before starting work another agent in the same swarm could plausibly also attempt (a specific file, task, ticket, or any named resource), when a claim() call returns a denial and you need to interpret it correctly, or when finishing or abandoning previously claimed work.
---

# Claim work with Roshambo

Roshambo's `claim` / `release` tools give a swarm of agents an exclusive, serializable
lease on a named resource, backed by CockroachDB's serializable isolation. Two agents
racing for the same resource get exactly one winner — no file locks, no polling, no
race window. Access these through the `roshambo-mcp` MCP server (see the sibling skill
`roshambo-remember-and-recall` for the memory side, `recall`/`remember`).

## Before starting shareable work: claim it

**Call `claim()` before starting work that another agent in the swarm could plausibly
also pick up** — a specific file to edit, a specific task or ticket, any named unit of
work more than one agent might reach for.

```
claim(resource="<a stable, descriptive identifier>", agent_id="<your agent id>",
      intent="<short, human-readable: what you plan to do>")
```

- `resource` should be a stable identifier another agent would independently arrive at
  for the same piece of work — for example `"repo:roshambo:src/memory.py"` or
  `"task:onboarding-flow"` — not something so specific it accidentally never collides,
  and not so generic it collides with unrelated work.
- `intent` is read by whoever gets denied, so write it for a human or another agent to
  understand at a glance, not just for yourself.
- The result's `_type` field is either `"Claim"` (you got it — proceed) or
  `"ClaimDenied"` (someone else has it right now).

## A denial is a normal result, not an error

**Do not treat `ClaimDenied` as a failure to retry in a loop.** It is Roshambo's way of
telling you who is already on this and what they intend:

```
{"_type": "ClaimDenied", "resource": "...", "held_by": "<agent_id>",
 "intent": "<what they said they'd do>", "expires_at": "..."}
```

React to a denial deliberately: pick different work, wait if the lease is close to
expiring and the work matters, or — if `intent` suggests it's relevant to what you were
about to do — go check what they found instead of duplicating it (this is exactly what
`recall()` is for; see `roshambo-remember-and-recall`). Retrying `claim()` in a tight
loop until it succeeds defeats the point: the other agent is doing real work, not just
holding a lock you should race past.

## During long work: heartbeat, don't just hold

A claim expires after its TTL (`ttl_seconds`, default from swarm configuration) whether
or not you are still working. If a task will plausibly run longer than the TTL, call
`heartbeat(claim_id)` periodically to extend it — do not assume a claim lives forever
once granted. An expired-but-unreleased claim still blocks other agents until it
actually lapses, so relying on "it'll still be there" instead of heartbeating risks
either losing the claim mid-task or blocking others longer than necessary.

## When finished or abandoning: release promptly

**Call `release(claim_id)` as soon as the claimed work is done — or as soon as you
decide to abandon it.** Do not wait for the TTL to expire; that just blocks other
agents for no reason. If you abandon the work, `release()` it and consider writing a
`remember()` trail explaining why (see `roshambo-remember-and-recall`) so the next agent
who claims the same resource does not repeat the abandonment blind.

## Related

- `roshambo-remember-and-recall` — writing and searching outcome trails; pairs
  naturally with claim/release around the same piece of work.
- `docs/mcp-managed.md` in this repository — the separate CockroachDB Managed MCP
  Server, used for read-only schema/operational inspection, not for coordination.
