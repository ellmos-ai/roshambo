"""Roshambo demo web app -- what the jury sees in the video.

Shows, live against a real CockroachDB cluster when one is configured:

* active claims, with who holds each one, what they intend to do, and their
  **origin system** (``framework``/``host``, via ``demo/queries.py``'s join
  against the ``agents`` table) -- Roshambo coordinates agents that don't know
  each other, so the demo has to show at least two *different* systems
  contending for a resource, not three copies of the same Lambda. See
  ``demo/run_collision_demo.py`` for the script that produces this.
* a recall() search box with a distance-ranked hit list and an outcome filter
  (beat 3: a new agent asks "has this been tried?" before repeating a mistake)

Run with ``ROSHAMBO_DSN`` unset and there is no live cluster to show -- rather
than crash, every endpoint falls back to small, clearly-labelled example
data (``"mode": "mock"`` in every response) so the UI itself is still
inspectable and the frontend contract can be exercised without
infrastructure. No cluster is provisioned for this demo by default -- set
``ROSHAMBO_DSN`` to a real one to switch to ``"mode": "live"``. This app and
``demo/queries.py``'s join have been verified in ``"mode": "live"`` against
a real (locally started, insecure single-node) CockroachDB cluster; see
docs/EVIDENCE-aws.md for exactly what was run and what wasn't (no AWS
credentials were available in that same session, so the Lambda side of the
collision was only exercised via ``--lambda-mode local-simulate``, never a
real deployed function).

Run: ``uvicorn demo.app:app --reload`` from the repo root (with ``src/`` on
``PYTHONPATH``, e.g. via ``pip install -e .`` or ``PYTHONPATH=src``). See
``demo/README.md``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("roshambo.demo")

STATIC_DIR = Path(__file__).parent / "static"
# The product is branded "Roshambo" (renamed 2026-07-25; the Python package/import
# path stays `roshambo` -- see docs/HANDOFF.md). `assets/` at the repo root holds the
# logo/favicon; mounted separately from `demo/static/` so those files stay the single
# shared source other surfaces (README, docs) also draw from, not copied in here.
ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
VALID_OUTCOMES = {"success", "failure", "abandoned", "inconclusive"}

# Nothing here decides *where* this app is served. Port and bind address are the
# caller's business (see `demo/serve.py`), and `ROSHAMBO_DEMO_ROOT_PATH` lets it sit
# behind a reverse proxy under a path prefix -- every URL the frontend builds is
# relative, so mounting at "/" or at "/roshambo/" both work without an edit. The
# host has not been chosen yet (see MANIFEST.md), and this file is not the place
# where that choice should have to be made.
app = FastAPI(title="Roshambo demo", root_path=os.environ.get("ROSHAMBO_DEMO_ROOT_PATH", ""))


# --------------------------------------------------------------------- state

class _Backend:
    """Lazily tries a real Roshambo connection once, then remembers whether it
    worked. A demo process that starts before a cluster exists (or loses one
    mid-run) should not need a restart to try again -- call `refresh()`.
    """

    def __init__(self) -> None:
        self.cfg = None
        self.roshambo = None
        self.mode = "mock"
        self.detail = "not yet attempted"
        self.refresh()

    def refresh(self) -> None:
        try:
            from roshambo.config import load_config
            from roshambo.memory import Roshambo
        except ImportError as exc:
            self.mode = "mock"
            self.detail = f"roshambo.config/roshambo.memory not importable: {exc}"
            return
        try:
            cfg = load_config()
        except Exception as exc:
            self.mode, self.detail = "mock", f"no usable ROSHAMBO_DSN: {exc}"
            return
        try:
            roshambo = Roshambo(cfg)
            roshambo.status()  # cheapest real round trip to prove the connection works
        except Exception as exc:
            self.mode, self.detail = "mock", f"ROSHAMBO_DSN set but connection failed: {exc}"
            return
        self.cfg, self.roshambo, self.mode, self.detail = cfg, roshambo, "live", "connected"
        logger.info("demo backend is live against swarm_id=%s", cfg.swarm_id)


backend = _Backend()


# ------------------------------------------------------------------- mock data

_MOCK_NOW = datetime.now(timezone.utc)

# Two *different* agent systems, not two instances of the same one -- see the module
# docstring. The second entry is denied-and-gone by the time a claim would be listed
# here (ClaimDenied is not persisted, only the winner holds a row in `claims`), so mock
# mode shows the winner's claim plus a static "recently denied" hint the frontend
# renders next to it; live mode gets the equivalent from an actual collision via
# demo/run_collision_demo.py plus the trail it writes for the losing side.
_MOCK_CLAIMS = [
    {
        "resource": "s3-prefix:roshambo-demo-bucket/agent-runs/2026-07-25T09-00-00/",
        "agent_id": "b3f1c2b0-2222-4a11-9a2b-000000000001",
        "intent": "apply the pending billing schema migration",
        "acquired_at": (_MOCK_NOW - timedelta(seconds=40)).isoformat(),
        "expires_at": (_MOCK_NOW + timedelta(seconds=260)).isoformat(),
        "framework": "aws-lambda-bedrock",
        "host": "aws-lambda:us-east-1:roshambo-worker",
    }
]

_MOCK_TRAILS = [
    {
        "trail_id": "mock-trail-1",
        "topic": "migrate billing schema",
        "approach": "ran the migration directly against the primary with no lock timeout",
        "outcome": "failure",
        "evidence": "migration blocked on a long-running report query, timed out after 30s",
        "agent_id": "b3f1c2b0-2222-4a11-9a2b-000000000002",
        "created_at": (_MOCK_NOW - timedelta(hours=2)).isoformat(),
        "artifact_uri": None,
        "distance": 0.083,
        "strength": 2.0,
    },
    {
        "trail_id": "mock-trail-2",
        "topic": "migrate billing schema",
        "approach": "added a short lock_timeout and retried on 55P03 before migrating",
        "outcome": "success",
        "evidence": "migration completed in 4.2s after two retries",
        "agent_id": "b3f1c2b0-2222-4a11-9a2b-000000000001",
        "created_at": (_MOCK_NOW - timedelta(hours=1)).isoformat(),
        "artifact_uri": None,
        "distance": 0.091,
        "strength": 1.0,
    },
    {
        "trail_id": "mock-trail-3",
        "topic": "migrate billing schema",
        "approach": "apply the pending billing schema migration",
        "outcome": "abandoned",
        "evidence": (
            "lost the race for s3-prefix:roshambo-demo-bucket/agent-runs/2026-07-25T09-00-00/: "
            "held by b3f1c2b0-2222-4a11-9a2b-000000000001 (apply the pending billing schema "
            "migration), expires " + (_MOCK_NOW + timedelta(seconds=260)).isoformat()
        ),
        "agent_id": "c4e2d3c1-3333-4b22-8b3c-000000000003",
        "created_at": (_MOCK_NOW - timedelta(seconds=39)).isoformat(),
        "artifact_uri": None,
        "distance": 0.02,
        "strength": 1.0,
    },
]


_MOCK_DENIALS = [
    {
        "trail_id": "mock-trail-3",
        "agent_id": "c4e2d3c1-3333-4b22-8b3c-000000000003",
        "topic": "migrate billing schema",
        "evidence": _MOCK_TRAILS[2]["evidence"],
        "created_at": _MOCK_TRAILS[2]["created_at"],
        "framework": "local-cli-agent",
        "host": "local-workstation-mock01",
        "resource": _MOCK_CLAIMS[0]["resource"],
        "held_by": _MOCK_CLAIMS[0]["agent_id"],
        "holder_framework": _MOCK_CLAIMS[0]["framework"],
        "holder_intent": _MOCK_CLAIMS[0]["intent"],
    }
]


def _mock_status() -> dict:
    return {"agents": 3, "active_claims": 1, "trails": 3, "failures": 1, "facts": 0}


# ------------------------------------------------------------------------ API


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    if backend.mode == "live":
        s = backend.roshambo.status()
        counters = {
            "agents": s.agents,
            "active_claims": s.active_claims,
            "trails": s.trails,
            "failures": s.failures,
            "facts": s.facts,
        }
        swarm_id = backend.cfg.swarm_id
    else:
        counters = _mock_status()
        swarm_id = "mock-swarm"
    return {"mode": backend.mode, "detail": backend.detail, "swarm_id": swarm_id, **counters}


@app.get("/api/claims")
def api_claims() -> dict[str, Any]:
    if backend.mode == "live":
        from demo.queries import active_claims

        try:
            claims = active_claims(backend.cfg)
        except Exception as exc:
            backend.refresh()
            raise HTTPException(status_code=503, detail=f"claims query failed: {exc}") from exc
    else:
        claims = _MOCK_CLAIMS
    return {"mode": backend.mode, "claims": claims}


@app.get("/api/denials")
def api_denials(limit: int = Query(10, ge=1, le=50)) -> dict[str, Any]:
    """Recently lost races -- beat 1's "Absage mit Angabe, wer arbeitet".

    A denial is only ever a return value, never a row of its own, so what is listed
    here are the ``outcome="abandoned"`` trails the losing workers wrote about
    themselves. See ``demo/queries.py:recent_denials`` for why that source beats
    ``audit_log``.
    """
    if backend.mode == "live":
        from demo.queries import recent_denials

        try:
            denials = recent_denials(backend.cfg, limit=limit)
        except Exception as exc:
            backend.refresh()
            raise HTTPException(status_code=503, detail=f"denials query failed: {exc}") from exc
    else:
        denials = _MOCK_DENIALS[:limit]
    return {"mode": backend.mode, "denials": denials}


@app.get("/api/recall")
def api_recall(
    query: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=50),
    outcomes: str | None = Query(None, description="comma-separated outcome filter"),
) -> dict[str, Any]:
    outcome_list: list[str] | None = None
    if outcomes:
        outcome_list = [o.strip() for o in outcomes.split(",") if o.strip()]
        unknown = [o for o in outcome_list if o not in VALID_OUTCOMES]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"unknown outcome(s) {unknown}, expected any of {sorted(VALID_OUTCOMES)}",
            )

    if backend.mode == "live":
        try:
            hits = backend.roshambo.recall(query=query, limit=limit, outcomes=outcome_list)
        except Exception as exc:
            backend.refresh()
            raise HTTPException(status_code=503, detail=f"recall failed: {exc}") from exc
        results = [
            {
                "trail_id": hit.trail.trail_id,
                "topic": hit.trail.topic,
                "approach": hit.trail.approach,
                "outcome": hit.trail.outcome,
                "evidence": hit.trail.evidence,
                "agent_id": hit.trail.agent_id,
                "created_at": hit.trail.created_at.isoformat(),
                "artifact_uri": hit.trail.artifact_uri,
                "distance": hit.distance,
                "strength": hit.strength,
            }
            for hit in hits
        ]
    else:
        results = [
            hit for hit in _MOCK_TRAILS if not outcome_list or hit["outcome"] in outcome_list
        ][:limit]

    return {"mode": backend.mode, "query": query, "hits": results}


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    return {"mode": backend.mode, "detail": backend.detail}


# --------------------------------------------------------------- static files

if STATIC_DIR.is_dir():

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
