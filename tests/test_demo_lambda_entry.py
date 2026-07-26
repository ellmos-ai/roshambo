"""The demo app answering AWS Lambda Function URL events (demo/lambda_entry.py).

The demo's target host is a Lambda Function URL, and no AWS account exists yet, so the
adapter cannot be proven by deploying it. What *can* be proven from here is that the
handler turns a Function-URL-shaped event into the same response uvicorn would produce --
and the places where an adapter goes wrong are not the obvious ones:

* **Binary responses.** The logo is a PNG. Mangum has to base64-encode it and say so in
  `isBase64Encoded`; a text-mode round trip would corrupt it silently and the page would
  simply lose its logo in production.
* **Query strings.** Payload format 2.0 carries them in a separate `rawQueryString`
  field, not inside `rawPath`. An adapter wired to the path alone passes a plain
  `/api/status` test and then drops every parameter `recall()` needs.
* **File responses.** `/` returns a `FileResponse`, not a JSON return value, so it goes
  through a different Starlette path than the API routes.

Events are written out by hand rather than pulled from a fixture library: the point is to
assert against the documented payload shape, not against another package's idea of it.

No cluster is needed. The app reports `"mode": "mock"` here, which is fine -- these tests
are about the adapter, not the data.
"""

from __future__ import annotations

import base64
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


def function_url_event(path: str, query: str = "") -> dict:
    """A GET as AWS delivers it to a Function URL (payload format 2.0).

    Shape per the AWS Lambda developer guide's "Payload format version 2.0": `rawPath`
    and `rawQueryString` are separate, and `requestContext.http` carries the method.
    """
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": query,
        "headers": {"host": "example.lambda-url.eu-central-1.on.aws", "accept": "*/*"},
        "requestContext": {
            "http": {
                "method": "GET",
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.7",
                "userAgent": "pytest",
            },
            "stage": "$default",
        },
        "isBase64Encoded": False,
    }


def test_json_route_answers_through_the_adapter() -> None:
    response = handler(function_url_event("/api/health"), None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["mode"] in {"live", "mock"}


def test_query_string_reaches_the_route() -> None:
    """`rawQueryString` is a separate field -- an adapter that ignores it looks fine on
    parameterless routes and then silently breaks every search."""
    response = handler(
        function_url_event("/api/recall", "query=billing+schema+migration&limit=3"), None
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["query"] == "billing schema migration"
    assert len(body["hits"]) <= 3


def test_a_rejected_query_string_still_comes_back_as_its_own_status() -> None:
    """A 400 from FastAPI's own validation has to survive the adapter as a 400, not
    become a 500 or an unhandled exception in the Lambda log."""
    response = handler(function_url_event("/api/recall", "query=x&outcomes=nonsense"), None)

    assert response["statusCode"] == 400


def test_index_route_returns_html() -> None:
    """`/` is a FileResponse, a different Starlette path than the JSON routes."""
    response = handler(function_url_event("/"), None)

    assert response["statusCode"] == 200
    assert "text/html" in response["headers"]["content-type"]
    assert "<title>Roshambo" in _decode(response)


def test_binary_asset_survives_the_round_trip() -> None:
    """The logo is a PNG. It has to come back base64-encoded, flagged as such, and byte
    for byte identical -- otherwise the deployed page quietly loses its branding."""
    on_disk = (REPO_ROOT / "assets" / "roshambo-favicon.png").read_bytes()

    response = handler(function_url_event("/assets/roshambo-favicon.png"), None)

    assert response["statusCode"] == 200
    assert response["isBase64Encoded"] is True
    assert base64.b64decode(response["body"]) == on_disk


def test_unknown_path_is_a_404_not_a_crash() -> None:
    response = handler(function_url_event("/does-not-exist"), None)

    assert response["statusCode"] == 404


def _decode(response: dict) -> str:
    body = response["body"]
    return base64.b64decode(body).decode("utf-8") if response["isBase64Encoded"] else body
