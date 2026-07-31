# Evidence: the three flagged defects of `poc-starmap-2026-07-30`, resolved

> Written 2026-07-31 by the succeeding coordinator (Kimi K3, Moonshot), after the
> orchestrating agent's handoff. Nothing in `PROTOCOL.md` or the historical evidence
> documents is altered by this file; it explains what the detector flagged and why the
> flags do not describe two simultaneously live leases.

`collect_evidence.py --swarm poc-starmap-2026-07-30` reports, as its only open item:

```
defects (two live leases)  3
```

All three are the same episode, inside 285 milliseconds, on one resource:

```
resource: starmap:task:06
2026-07-30T22:24:52.585002+00:00  denied, reason "held by BotAgent-C@WORKSTATION-LG"
2026-07-30T22:24:52.656211+00:00  denied, reason "held by BotAgent-C@WORKSTATION-LG"
2026-07-30T22:24:52.870608+00:00  denied, reason "held by BotAgent-C@WORKSTATION-LG"
last grant the detector had seen: BotAgent-E@WORKSTATION-LG at 22:24:40.987356
```

The detector walks `audit_log` in `created_at` order and flags a denial that names a
holder other than the most recent grant it has seen. For the flag to mean "two live
leases", BotAgent-E's lease would have had to be alive while BotAgent-C also held the
resource. The raw log shows otherwise.

## The raw record (server `audit_log`, unedited)

BotAgent-E@WORKSTATION-LG, all verbs, 22:24:35–22:25:35 (release rows carry no resource
column; the claim/release interleave attributes them):

```
22:24:40.987  claim    starmap:task:06  allowed   latency 231 ms
22:24:44.176  release  —                allowed   latency 125 ms   <- task:06 handed back
22:24:47.564  claim    starmap:task:07  allowed   latency 113 ms
22:24:51.709  release  —                allowed   latency 916 ms
```

The contended window, all agents, 22:24:38–22:24:54:

```
22:24:40.987  claim   BotAgent-E@WORKSTATION-LG  starmap:task:06  allowed
22:24:42.111  claim   BotAgent-B@ASUS-GEI        starmap:task:06  denied "held by E"
22:24:44.176  release BotAgent-E@WORKSTATION-LG                    allowed
22:24:52.585  claim   BotAgent-B@WORKSTATION-LG  starmap:task:06  denied "held by C"
22:24:52.656  claim   BotAgent-D@WORKSTATION-LG  starmap:task:06  denied "held by C"
22:24:52.870  claim   BotAgent-A@WORKSTATION-LG  starmap:task:06  denied "held by C"
22:24:52.904  claim   BotAgent-C@WORKSTATION-LG  starmap:task:06  ALLOWED  latency 2321 ms
```

## What happened, in order

1. E takes `starmap:task:06` at 40.987, hands it back at 44.176. The resource is free.
2. C calls `claim`. Its recorded latency is 2321 ms: the call began around 50.6, and its
   acquisition statement committed some time before 52.585.
3. B, D and A call `claim` at 52.585–52.870, find C holding, and are denied — correctly,
   each naming C.
4. C's own audit row is written at 52.904, **after** the three denials it caused.

Step 4 is the whole mystery. `Roshambo.claim()` (`src/roshambo/memory.py:164`) first
calls `acquire()`, then writes the audit row as a separate statement
(`self._audit(...)`); the connection runs `autocommit=True` (`src/roshambo/db.py:35`),
and `audit_log.created_at` defaults to `now()` — the timestamp of the audit statement's
own transaction. The lease is therefore visible to other agents *before* its grant row
exists in the log. Across a Wide Area Network round trip (here over two seconds for C),
the grant row can land later than denials that were caused by the grant. The detector,
which reads the log in `created_at` order, sees a denial naming C while its most recent
grant is still E — and flags a defect. The defect is in the detector's ordering
assumption, not in the lease history.

## Why this was never two live leases

Acquisition is one atomic statement (`ACQUIRE_SQL`, `src/roshambo/leases.py:27`):
`INSERT ... ON CONFLICT DO UPDATE ... WHERE claims.expires_at < now()`. The database
decides grant or refusal inside a single statement; there is no client-side
check-then-write window. C's claim being *allowed* is itself the server's verdict that
no live lease blocked it — E had released the resource eight seconds earlier.

Every element of the benign reading is independently in the log: E's release (44.176),
the absence of any denial naming E after it, C's allowed grant, and three denials
correctly naming C. The two-live-leases reading requires E's lease to be alive at
52.585, which contradicts both the release row and the server's decision to grant C.

## What this does and does not establish

- The honest claim is unchanged: **no resource was ever held by two agents at once.**
  The guarantee rests on the single-statement acquisition, and it survived 1226 genuine
  collisions, 147 of them between two machines, with 1245 of 1245 denials naming the
  actual holder.
- `collect_evidence.py` remains the only verified detector. Its "defect" class is now
  understood precisely: it detects *audit-order anomalies*, of which these three are the
  only ones in 4011 rows, and each requires the case-level reconstruction done here.
  That limitation is a property of any after-the-fact reader of an append-only log whose
  writer lags the decision it logs; it is not a property of the coordination itself.
- The count staying at exactly 3 while collisions grew from 162 to 1226 is explained:
  the episode needed a slow (2321 ms) winning claim racing three fast losers inside one
  285 ms window — a rare alignment, not an ongoing fault.
