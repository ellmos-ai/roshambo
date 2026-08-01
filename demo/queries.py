"""Direct-SQL reads not (yet) covered by ``roshambo.memory.Roshambo``'s high-level API.

``Roshambo.status()`` returns only a *count* of active claims, and there is no
bulk claim-listing method on the frozen interface. The demo needs "active
claims with holder and intent" as a live list (MANIFEST.md section 7, beat
1: "Drei Agenten ... Einer bekommt den Lease, zwei bekommen eine Absage mit
Angabe, wer gerade arbeitet ... Sichtbar in der Web-UI"), so this module
reads the ``claims`` table directly through ``roshambo.db``'s generic SQL
helpers (the same ones ``roshambo.memory`` itself is built on).

This is a read-only, demo-only workaround, not part of the frozen
``CONTRACT.md`` interface, and it is flagged as a request in
``docs/HANDOFF.md`` (dated 2026-07-25): a ``Roshambo.list_active_claims()``
convenience method on Core's side would let the demo go through the same
audited path as everything else and let this module be deleted.

``active_claims`` also reports each claim's **origin system** (``framework``/
``host``, from ``schema/001_init.sql``'s ``agents`` table): the demo's whole
point is showing at least two *different* agent runtimes contending for the
same resource, not three copies of the same Lambda, so a claim without a
visible origin system would undersell exactly the thing being demonstrated.
Claims now reference ``agents.agent_key`` and carry the framework/host snapshot
captured when the lease was granted. Reading that snapshot instead of the current
registry row is important: re-registering an identity on another machine must not
rewrite what an earlier claim proved.
"""

from __future__ import annotations

from roshambo.config import RoshamboConfig
from roshambo.db import connect, fetch_all


def active_claims(cfg: RoshamboConfig) -> list[dict]:
    """Every non-expired claim in ``cfg.swarm_id``, most recently acquired first,
    each annotated with the immutable framework/host snapshot captured at grant time,
    plus a live ``display_name`` for the UI.

    ``display_name`` is deliberately *not* part of the snapshot: unlike
    ``framework``/``host`` (evidence -- what a claim actually proved, frozen so a
    later re-registration cannot rewrite it), a display name is purely cosmetic
    labelling (``agents.capabilities->>'display_name'``, see
    ``demo/multivendor/bot_agent.py`` and ``run_field.py``'s
    ``annotate_display_names``), so reading the current registry row for it is fine
    -- and it may simply be absent (``None``) for an agent that never had one set,
    which the frontend falls back to the raw ``agent_id`` for.
    """
    with connect(cfg) as conn:
        rows = fetch_all(
            conn,
            """
            SELECT c.resource, c.agent_id, c.intent, c.acquired_at, c.expires_at,
                   c.framework_snapshot, c.host_snapshot, a.capabilities->>'display_name'
              FROM claims c
              LEFT JOIN agents a
                ON a.swarm_id = c.swarm_id AND a.agent_key = c.agent_id
             WHERE c.swarm_id = %s AND c.expires_at > now()
             ORDER BY c.acquired_at DESC
            """,
            (cfg.swarm_id,),
        )
    return [
        {
            "resource": resource,
            "agent_id": str(agent_id),
            "intent": intent,
            "acquired_at": acquired_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "framework": framework,
            "host": host,
            "display_name": display_name,
        }
        for (
            resource,
            agent_id,
            intent,
            acquired_at,
            expires_at,
            framework,
            host,
            display_name,
        ) in rows
    ]


def recent_denials(cfg: RoshamboConfig, limit: int = 10) -> list[dict]:
    """The last ``limit`` losses of a race, newest first.

    MANIFEST.md section 7, beat 1 asks for two things to be visible in the UI: who
    holds the lease, *and* that the others were turned away **naming who is working**.
    A ``ClaimDenied`` is not persisted as such -- only the winner leaves a row in
    ``claims`` -- so the record of a denial is the ``outcome="abandoned"`` trail the
    losing worker writes about itself.

    Read from ``trails`` rather than from ``audit_log``: the audit row carries the
    holder only as free text inside ``reason`` (``"held by <uuid>"``), and the
    holder's intent would have to be looked up in ``claims``, where it disappears the
    moment the lease is released. The trail carries resource, holder and intent as
    written at denial time and keeps them afterwards. ``detail`` is where
    ``demo/run_story.py``'s workers put those fields structurally
    (``kind="claim-denied"``); ``demo/local_agent_worker.py`` writes the same facts
    into ``evidence`` prose only, so callers must treat ``detail`` as possibly empty
    instead of relying on it.

    No vector is involved, which is deliberate: the panel has to be correct no matter
    which embedder wrote the trail (see ``roshambo.memory.PLACEHOLDER_PROVIDER``).
    """
    with connect(cfg) as conn:
        rows = fetch_all(
            conn,
            """
            SELECT t.trail_id, t.agent_id, t.topic, t.evidence, t.detail, t.created_at,
                   a.framework, a.host, a.capabilities->>'display_name',
                   h.framework, h.capabilities->>'display_name'
              FROM trails t
              LEFT JOIN agents a
                ON a.swarm_id = t.swarm_id AND a.agent_key = t.agent_id
              LEFT JOIN agents h
                ON h.swarm_id = t.swarm_id AND h.agent_key = t.detail->>'held_by'
             WHERE t.swarm_id = %s AND t.outcome = 'abandoned'
             ORDER BY t.created_at DESC
             LIMIT %s
            """,
            (cfg.swarm_id, limit),
        )
    denials = []
    for (
        trail_id,
        agent_id,
        topic,
        evidence,
        detail,
        created_at,
        framework,
        host,
        display_name,
        holder_framework,
        holder_display_name,
    ) in rows:
        detail = detail or {}
        denials.append(
            {
                "trail_id": str(trail_id),
                "agent_id": None if agent_id is None else str(agent_id),
                "topic": topic,
                "evidence": evidence,
                "created_at": created_at.isoformat(),
                "framework": framework,
                "host": host,
                "display_name": display_name,
                "resource": detail.get("resource"),
                "held_by": detail.get("held_by"),
                # Resolved by join, not stored: a denied worker learns the holder's
                # id and intent from `ClaimDenied`, but not which runtime that id
                # belongs to. Looking it up here keeps the answer current instead of
                # freezing whatever the loser could see at denial time.
                "holder_framework": holder_framework,
                "holder_display_name": holder_display_name,
                "holder_intent": detail.get("holder_intent"),
            }
        )
    return denials
