"""Run the demo web app as an AWS Lambda function behind a Function URL.

The target host for the demo is a Lambda Function URL (user decision, 2026-07-26). This
module is the whole of what that requires: one adapter over the same `demo.app:app` that
`demo/serve.py` runs locally. There is deliberately no second code path -- an endpoint
that only exists in the deployed copy is an endpoint nobody tests.

    # Lambda handler:  demo.lambda_entry.handler
    # Local:           python demo/serve.py --dev

Two properties of the app make this a thin adapter rather than a port, and both should
stay true:

* **It polls; it never opens a socket.** A Function URL cannot do WebSockets. The
  frontend refreshes over ordinary GETs (`demo/static/app.js`), which is also why the
  demo needs no per-client server state.
* **Every URL it builds is relative.** A Function URL serves at the domain root, so the
  question does not arise there -- but it means the same build also works behind a
  reverse proxy under a path prefix, which is what the fallback host would need.

`lifespan="off"` because the app registers no startup or shutdown handlers: there is
nothing for the lifespan protocol to carry. If one is ever added, this has to change to
`"auto"` or the handler will never run it.

**Deployed since 2026-07-30** behind a public Function URL in ``eu-central-1`` (see
`demo/README.md`, "Deployed", for what was verified about the deployment itself --
this paragraph used to say "not deployed" and predates that). Independent of any
deployment, the handler's contract is verified from here: it answers
Function-URL-shaped events correctly, including a binary asset and a query string
(`tests/test_demo_lambda_entry.py`).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from mangum import Mangum  # noqa: E402

from demo.app import app  # noqa: E402

handler = Mangum(app, lifespan="off")
