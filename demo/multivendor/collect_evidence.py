"""Turn one swarm's `audit_log` into the numbers registered in PROTOCOL.md.

The agents' own reports are hearsay. An agent can misread its exit code, or report a
success it did not have -- and two of the three here are third-party runtimes whose
narration we have no reason to trust more than anyone else's. `audit_log` is written
by the code path that actually made the decision, inside the same transaction-bearing
connection, so it is the only witness worth quoting.

Every rule applied here is stated in PROTOCOL.md, which was committed before the run.
Nothing in this file may introduce a new definition.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rsb import resolve_dsn  # noqa: E402

VENDOR_OF = {
    "claude-code": "anthropic",
    "codex": "openai",
    "agy": "google",
}

TASK_PREFIX = "fieldkit:task:"
INDEX_RESOURCE = "fieldkit:index"

HELD_BY = "held by "


@dataclass
class Event:
    created_at: datetime
    agent_id: str | None
    resource: str | None
    allowed: bool
    reason: str | None


@dataclass
class Collision:
    resource: str
    denied_agent: str
    holder: str
    granted_at: datetime
    denied_at: datetime

    @property
    def gap_seconds(self) -> float:
        return (self.denied_at - self.granted_at).total_seconds()

    @property
    def cross_vendor(self) -> bool:
        """True only when both sides are known agents from different vendors.

        An unknown agent id is never counted as cross-vendor. Treating "unknown"
        as "different" would let a stray manual claim inflate the one figure the
        whole claim rests on.
        """
        denied = VENDOR_OF.get(self.denied_agent)
        holder = VENDOR_OF.get(self.holder)
        return bool(denied and holder and denied != holder)


@dataclass
class Analysis:
    resource_class: str
    denials: int = 0
    collisions: list[Collision] = field(default_factory=list)
    stale_denials: int = 0
    defects: list[dict] = field(default_factory=list)

    @property
    def contention_events(self) -> int:
        """Distinct (grant, denying agent) pairs -- retries collapse to one."""
        return len({(c.resource, c.granted_at, c.denied_agent) for c in self.collisions})

    @property
    def cross_vendor_collisions(self) -> list[Collision]:
        return [c for c in self.collisions if c.cross_vendor]

    @property
    def cross_vendor_events(self) -> int:
        return len(
            {(c.resource, c.granted_at, c.denied_agent) for c in self.cross_vendor_collisions}
        )


def fetch_events(dsn: str, swarm_id: str) -> list[Event]:
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT created_at, agent_id, resource, allowed, reason
            FROM audit_log
            WHERE swarm_id = %s AND verb = 'claim'
            ORDER BY created_at ASC
            """,
            (swarm_id,),
        )
        return [Event(*row) for row in cur.fetchall()]


def fetch_counts(dsn: str, swarm_id: str) -> dict[str, int]:
    import psycopg

    counts = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for label, sql in (
            ("trails", "SELECT count(*) FROM trails WHERE swarm_id = %s"),
            (
                "trail_failures",
                "SELECT count(*) FROM trails WHERE swarm_id = %s AND outcome = 'failure'",
            ),
            ("audit_rows", "SELECT count(*) FROM audit_log WHERE swarm_id = %s"),
            (
                "distinct_agents",
                "SELECT count(DISTINCT agent_id) FROM audit_log "
                "WHERE swarm_id = %s AND agent_id IS NOT NULL",
            ),
        ):
            cur.execute(sql, (swarm_id,))
            counts[label] = cur.fetchone()[0]
    return counts


def classify(resource: str | None) -> str:
    if resource is None:
        return "other"
    if resource.startswith(TASK_PREFIX):
        return "task"
    if resource == INDEX_RESOURCE:
        return "index"
    return "other"


def analyse(events: list[Event], ttl_seconds: int) -> dict[str, Analysis]:
    """Apply the rules from PROTOCOL.md section 5, verbatim."""
    window = timedelta(seconds=ttl_seconds)
    analyses = {name: Analysis(name) for name in ("task", "index", "other")}
    # Most recent grant seen per resource, walking forward in time.
    last_grant: dict[str, tuple[str, datetime]] = {}

    for event in events:
        resource = event.resource or ""
        bucket = analyses[classify(event.resource)]

        if event.allowed:
            last_grant[resource] = (event.agent_id or "?", event.created_at)
            continue

        bucket.denials += 1

        named = ""
        if event.reason and event.reason.startswith(HELD_BY):
            named = event.reason[len(HELD_BY) :].strip()

        grant = last_grant.get(resource)
        if grant is None:
            bucket.stale_denials += 1
            continue

        holder, granted_at = grant
        if event.created_at - granted_at > window:
            bucket.stale_denials += 1
            continue

        if named != holder:
            # A denial naming somebody other than the live lease holder would mean
            # two leases were alive at once. That is a correctness failure, not a
            # collision, and is reported as one.
            bucket.defects.append(
                {
                    "resource": resource,
                    "denied_at": event.created_at.isoformat(),
                    "named_holder": named,
                    "actual_holder": holder,
                }
            )
            continue

        bucket.collisions.append(
            Collision(
                resource=resource,
                denied_agent=event.agent_id or "?",
                holder=holder,
                granted_at=granted_at,
                denied_at=event.created_at,
            )
        )

    return analyses


def summarise(analyses: dict[str, Analysis], counts: dict[str, int], ttl: int) -> dict:
    def pack(analysis: Analysis) -> dict:
        pairs = Counter((c.denied_agent, c.holder) for c in analysis.collisions)
        return {
            "denials": analysis.denials,
            "genuine_collisions": len(analysis.collisions),
            "contention_events": analysis.contention_events,
            "cross_vendor_collisions": len(analysis.cross_vendor_collisions),
            "cross_vendor_events": analysis.cross_vendor_events,
            "stale_denials": analysis.stale_denials,
            "defects": analysis.defects,
            "informative_denials": len(analysis.collisions),
            "pairs": [
                {"denied": denied, "holder": holder, "count": n}
                for (denied, holder), n in sorted(pairs.items(), key=lambda kv: -kv[1])
            ],
            "resources": sorted({c.resource for c in analysis.collisions}),
        }

    return {
        "ttl_seconds": ttl,
        "counts": counts,
        "task_resources": pack(analyses["task"]),
        "index_resource": pack(analyses["index"]),
        "other_resources": pack(analyses["other"]),
    }


def render(summary: dict) -> str:
    lines: list[str] = []
    counts = summary["counts"]
    lines.append(
        f"audit rows: {counts['audit_rows']}  |  distinct agents: {counts['distinct_agents']}"
        f"  |  trails: {counts['trails']} ({counts['trail_failures']} failure)"
    )
    lines.append(f"lease TTL used for the window test: {summary['ttl_seconds']}s")
    lines.append("")

    for key, title in (
        ("task_resources", "TASK RESOURCES (the number that matters)"),
        ("index_resource", "INDEX RESOURCE (deliberate serialization point)"),
        ("other_resources", "OTHER RESOURCES"),
    ):
        block = summary[key]
        if not block["denials"] and not block["genuine_collisions"]:
            lines.append(f"{title}: no denials recorded")
            lines.append("")
            continue
        lines.append(title)
        lines.append(f"  denials recorded          {block['denials']}")
        lines.append(f"  genuine collisions        {block['genuine_collisions']}")
        lines.append(f"  distinct contention events{block['contention_events']:>3}")
        lines.append(f"  cross-vendor collisions   {block['cross_vendor_collisions']}")
        lines.append(f"  cross-vendor events       {block['cross_vendor_events']}")
        lines.append(f"  stale denials (not counted){block['stale_denials']:>2}")
        lines.append(f"  defects (two live leases)  {len(block['defects'])}")
        for pair in block["pairs"]:
            lines.append(f"    {pair['denied']} was refused by {pair['holder']} x{pair['count']}")
        if block["resources"]:
            lines.append(f"  resources: {', '.join(block['resources'])}")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--swarm", required=True)
    parser.add_argument("--ttl", type=int, default=120)
    parser.add_argument("--json-out", help="write the full summary here")
    args = parser.parse_args(argv)

    dsn = resolve_dsn(dict(os.environ))
    events = fetch_events(dsn, args.swarm)
    counts = fetch_counts(dsn, args.swarm)
    summary = summarise(analyse(events, args.ttl), counts, args.ttl)

    print(render(summary))

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"written: {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
