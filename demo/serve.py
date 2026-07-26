"""Start the demo web app. The one command the ``--dev`` walkthrough uses.

    python demo/serve.py --dev

Exists so that "how do I run this" has a single answer that works from any working
directory, and so that no port, host or URL is hard-coded anywhere in the app: this
script reads all three from the environment and hands them to uvicorn.

    ROSHAMBO_DEMO_HOST       bind address     (default 127.0.0.1)
    ROSHAMBO_DEMO_PORT       port             (default 8000)
    PORT                     port, fallback   (what most managed hosts set)
    ROSHAMBO_DEMO_ROOT_PATH  path prefix when behind a reverse proxy (default "")

``--dev`` adds ``--reload`` and binds loopback; without it the default is still
loopback, because a demo endpoint with no authentication (see ``demo/README.md``)
should not start listening on every interface just because someone forgot a flag.
Pass ``--host 0.0.0.0`` (or set ``ROSHAMBO_DEMO_HOST``) to do that deliberately.

The app polls over plain HTTP GETs (``demo/static/app.js``) and holds no per-client
server state, so it also runs unchanged on a host that cannot do WebSockets -- an
AWS Lambda Function URL, for instance. That is not an accident and should stay true.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (REPO_ROOT, REPO_ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def resolve_port(env: dict[str, str] | None = None) -> int:
    """``ROSHAMBO_DEMO_PORT`` wins over ``PORT`` wins over 8000.

    ``PORT`` is honoured because that is the variable managed hosts inject; the
    prefixed one exists so a developer can override it locally without fighting
    whatever else on the machine also reads ``PORT``.
    """
    env = os.environ if env is None else env
    for name in ("ROSHAMBO_DEMO_PORT", "PORT"):
        raw = (env.get(name) or "").strip()
        if raw:
            try:
                return int(raw)
            except ValueError:
                raise SystemExit(f"error: {name}={raw!r} is not a port number") from None
    return DEFAULT_PORT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dev", action="store_true", help="reload on file change")
    parser.add_argument("--host", default=None, help="overrides ROSHAMBO_DEMO_HOST")
    parser.add_argument("--port", type=int, default=None, help="overrides ROSHAMBO_DEMO_PORT/PORT")
    args = parser.parse_args(argv)

    host = args.host or os.environ.get("ROSHAMBO_DEMO_HOST") or DEFAULT_HOST
    port = args.port if args.port is not None else resolve_port()

    import uvicorn

    # Import the app through its module path (not the object) so --reload works.
    print(f"Roshambo demo on http://{host}:{port}/  (dev={args.dev})", file=sys.stderr)
    print(
        "check the mode before recording anything: "
        f"curl http://{host}:{port}/api/health  -> expect \"mode\":\"live\"",
        file=sys.stderr,
    )
    uvicorn.run("demo.app:app", host=host, port=port, reload=args.dev)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
