# Screenshots

Real screenshots of the demo web app running against the CockroachDB Cloud cluster on
2026-07-26, swarm `demo-2026-07-26`. Every one was taken with the app reporting
`{"mode":"live","detail":"connected"}` — no mock data, no compositing, no retouching.
The numbers, ids and timestamps visible in them are the ones recorded in
[`../EVIDENCE-demo.md`](../EVIDENCE-demo.md).

Taken with headless Chrome against the running app:

```bash
chrome --headless=new --hide-scrollbars --window-size=1400,1160 \
       --virtual-time-budget=9000 --screenshot=01-collision.png \
       "http://127.0.0.1:8000/"
```

To reproduce them, follow the walkthrough in [`../../demo/README.md`](../../demo/README.md)
and take a shot between beats.

| File | Beat | What it shows |
|---|---|---|
| `01-collision.png` | 1 | Three agents went for one resource. `mcp-agent` holds the lease; **Turned Away** shows the other two, each told who is working and on what. Counters: 3 agents, 1 active claim, 2 trails. |
| `02-recall-all-outcomes.png` | 3 | The reworded query against every outcome. The failure is the third hit (distance 0.611, strength 2.0 after being reinforced) behind the two abandoned trails from beat 1 — the honest ranking, see EVIDENCE-demo.md §4. |
| `03-recall-failures-only.png` | 3 | The same query restricted to failures: the dead end, first and alone, with the SQLSTATE 55P03 evidence a later agent needs. |
| `04-lease-taken-over.png` | 4 | After the holder went silent: `notebook-agent` now holds the failover resource. The denials from beat 1 are still listed — they are trails, not locks, so they outlive the lease. |

Agent ids are per-run UUIDs, host labels are synthetic (`on-prem-batch-node-3`,
`mcp-gateway-eu-central-1`, `analytics-notebook-07`) — no real machine name, and no
connection string, appears in any image.
