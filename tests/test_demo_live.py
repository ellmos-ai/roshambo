"""The live map (demo/static/live.html + GET /api/live) in mock mode.

The live map is the replay viewer's projection pointed at the present: one read-only
snapshot endpoint (roster + active claims + recent audit events) and one static page
that polls it. These tests go through the same Lambda Function URL adapter as
``test_demo_lambda_entry.py`` on purpose -- the map's target host is the deployed
Function URL, so proving the route through the adapter proves the shape that actually
ships, not just what uvicorn would serve locally.

No cluster is needed: the app answers ``"mode": "mock"`` here, and the assertions are
about the snapshot's *shape* -- the contract ``live.html`` renders from -- not about
live data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="demo app dependencies not installed")
pytest.importorskip("mangum", reason="mangum is only needed for the Lambda entry point")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from demo.lambda_entry import handler  # noqa: E402
from tests.test_demo_lambda_entry import function_url_event  # noqa: E402


def get_json(path: str, query: str = "") -> tuple[int, dict]:
    resp = handler(function_url_event(path, query), context=None)
    body = json.loads(resp["body"]) if resp.get("body") else {}
    return resp["statusCode"], body


def test_api_live_mock_snapshot_shape() -> None:
    status, body = get_json("/api/live")
    assert status == 200
    assert body["mode"] == "mock"
    assert body["swarm_id"] == "mock-swarm"
    assert "now" in body
    counters = body["counters"]
    for key in ("agents", "active_claims", "trails", "failures", "facts"):
        assert isinstance(counters[key], int)
    assert isinstance(body["agents"], list) and body["agents"]
    for agent in body["agents"]:
        assert agent["agent_id"]
        assert agent["framework"]
        assert agent["host"]
        assert "last_heartbeat" in agent
    assert isinstance(body["claims"], list)
    assert isinstance(body["events"], list) and body["events"]
    for event in body["events"]:
        assert "created_at" in event
        assert "verb" in event
        assert "allowed" in event


def test_api_live_mock_spans_two_hosts() -> None:
    """The map's whole point is grouping by host; the mock must exercise it."""
    _, body = get_json("/api/live")
    hosts = {agent["host"] for agent in body["agents"]}
    assert len(hosts) >= 2


def test_api_live_mock_contains_a_denial_naming_the_holder() -> None:
    """live.html's conflict flash parses "held by <id>" from a denial's reason --
    the same convention the replay viewer and the audit log use."""
    _, body = get_json("/api/live")
    denials = [e for e in body["events"] if e["verb"] == "claim" and not e["allowed"]]
    assert denials, "mock events must include at least one denial"
    assert any("held by " in (e.get("reason") or "") for e in denials)


def test_api_live_never_serves_recall_events() -> None:
    """A recall's ``resource`` is a visitor's free-text search from the public demo's
    search box; serving it on ``/api/live`` would broadcast one visitor's input to
    every watcher of the map. The mock event list deliberately contains a recall row
    (demo/app.py) so this asserts the filter, not the absence of data."""
    from demo.app import _MOCK_EVENTS

    assert any(e["verb"] == "recall" for e in _MOCK_EVENTS)
    _, body = get_json("/api/live")
    assert all(e["verb"] != "recall" for e in body["events"])


def test_api_live_events_window_is_bounded() -> None:
    _, body = get_json("/api/live", "events=2")
    assert len(body["events"]) <= 2
    status, _ = get_json("/api/live", "events=0")
    assert status == 422  # ge=1 -- the window cannot be empty or negative


def test_live_page_is_served() -> None:
    resp = handler(function_url_event("/live"), context=None)
    assert resp["statusCode"] == 200
    assert "text/html" in resp["headers"].get("content-type", "")
    import base64

    html = (
        base64.b64decode(resp["body"]).decode("utf-8")
        if resp.get("isBase64Encoded")
        else resp["body"]
    )
    assert "live map" in html
    assert "api/live" in html  # relative on purpose; see the page's head comment


def test_index_links_to_the_live_map() -> None:
    resp = handler(function_url_event("/"), context=None)
    import base64

    html = (
        base64.b64decode(resp["body"]).decode("utf-8")
        if resp.get("isBase64Encoded")
        else resp["body"]
    )
    assert 'href="live"' in html
