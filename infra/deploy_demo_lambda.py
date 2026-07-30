#!/usr/bin/env python3
"""Package, deploy, and tear down the ``roshambo-demo`` Lambda Function URL.

Plain boto3 + zipfile, matching ``deploy_lambda.py`` (see ``infra/README.md`` for why this
repository does not use SAM/CDK/CloudFormation). This script covers the **demo web app**
(``demo/app.py`` behind ``demo/lambda_entry.py``'s Mangum adapter) -- a different function
from ``deploy_lambda.py``'s ``roshambo-worker``, which runs the autonomous Bedrock worker.
The two are independent: this one needs no Bedrock and no S3, only read access to the
CockroachDB cluster.

Subcommands
-----------

``package``      Build ``roshambo-demo.zip``: ``demo/`` (app + Lambda adapter + static
                 frontend, not the standalone scripts), ``src/roshambo`` (frozen import
                 path ``demo/lambda_entry.py`` relies on), ``assets/`` (logo/favicon
                 ``demo/app.py`` mounts at ``/assets``), the CockroachDB TLS root
                 certificate, and the third-party runtime dependencies (``mangum``,
                 ``fastapi``, ``starlette``, ``pydantic``, ``psycopg[binary]``) as
                 Lambda-compatible (manylinux) wheels regardless of the platform this
                 script runs on. Deliberately **no** ``uvicorn`` -- Mangum replaces it.
``create-role``  Create or update the least-privilege execution role: CloudWatch Logs
                 only (``AWSLambdaBasicExecutionRole``), nothing else. The demo only
                 reads from CockroachDB over the network; it needs no AWS permission
                 beyond writing its own log group.
``deploy``       Create or update the Lambda function from a packaged zip, with a
                 **reserved concurrency of 5** as a hard cost brake on a publicly
                 invokable endpoint (see "Cost guard" below).
``enable-url``   Create the public Function URL (``AuthType=NONE``) and the matching
                 resource policy statement that actually allows anonymous invocation --
                 the two are easy to conflate; ``AuthType=NONE`` alone still returns
                 403 without the ``add_permission`` call this subcommand also makes.
``teardown``     Best-effort delete of the function, its Function URL, its role, and
                 its log group. Not a transactional stack deletion, same caveat as
                 ``deploy_lambda.py``.

Why a second script instead of extending ``deploy_lambda.py``
---------------------------------------------------------------

``deploy_lambda.py`` packages ``src/roshambo`` alone at the zip root (handler
``roshambo.aws.worker.lambda_handler``) and its execution role needs Bedrock + S3. This
function packages ``demo/`` **and** ``src/roshambo`` side by side (handler
``demo.lambda_entry.handler``, which inserts ``/var/task`` and ``/var/task/src`` onto
``sys.path`` itself -- see that file), pulls in an entirely different dependency set (a
web framework, not a Bedrock client), and its role needs nothing but log-write access.
Folding both into one script's argument surface would make every flag conditional on
which function is being built; two small scripts stay legible.

Cost guard
----------

This endpoint has no authentication (see ``demo/README.md``, "Not built here") and its
Function URL is meant to be shared publicly for a hackathon demo. Two independent caps
keep an accidental traffic spike or scraper from being expensive: ``ReservedConcurrentExecutions=5``
here (at most 5 concurrent invocations regardless of request volume) and the AWS Budget
already configured on the account (``roshambo-hackathon-cap``, 100 USD/month, checked
before this script existed). Both are cost brakes, not security controls -- there is
still no auth on the endpoint itself.

TLS certificate decision
-------------------------

The configured cluster's certificate chains to ISRG Root X1 (Let's Encrypt) -- a public
CA, but that does not make ``sslrootcert`` optional. libpq's ``sslmode=verify-full``
looks for a root certificate at an explicit path (or ``~/.postgresql/root.crt``, which
does not exist inside a fresh Lambda execution environment); it does not fall back to
the OS/OpenSSL system trust store unless the connection string says ``sslrootcert=system``,
a libpq 16+ behaviour this project does not assume. So the certificate is bundled into
the deployment package (``certs/root.crt``) and ``deploy`` rewrites the DSN's
``sslrootcert`` to point at its path inside the running function
(``/var/task/certs/root.crt``) rather than relying on anything being present on the
host. This mirrors ``demo/README.md``'s own troubleshooting section, which already tells
a developer running locally to do the same thing by hand.

Nothing in this file embeds the certificate, the DSN, or any other credential -- the
certificate is read from a path given via ``--cert-source``/``ROSHAMBO_DEMO_TLS_ROOT_CERT``
at package time (never defaulted to a personal machine path, per ``CONTRACT.md``'s ground
rule against absolute personal paths in this repository), and the DSN comes from
``ROSHAMBO_DSN`` in the deploying shell's environment at deploy time, exactly like
``deploy_lambda.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = REPO_ROOT / "demo"
SRC_DIR = REPO_ROOT / "src" / "roshambo"
ASSETS_DIR = REPO_ROOT / "assets"
INFRA_DIR = Path(__file__).resolve().parent
BUILD_DIR = INFRA_DIR / "build"
DEFAULT_ZIP_PATH = BUILD_DIR / "roshambo-demo.zip"

FUNCTION_NAME = "roshambo-demo"
ROLE_NAME = "roshambo-demo-lambda-role"
HANDLER = "demo.lambda_entry.handler"
BASIC_EXECUTION_POLICY_ARN = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

# The Lambda runtime unpacks the deployment zip to this fixed path -- see
# docs.aws.amazon.com/lambda/latest/dg/python-package.html.
LAMBDA_TASK_ROOT = "/var/task"
CERT_ARCNAME = "certs/root.crt"

# Only the files the running app actually imports -- not the standalone scripts
# (`run_collision_demo.py`, `run_story.py`, `local_agent_worker.py`) or `multivendor/`,
# which are demo tooling, not part of the served app. Keeping the package to what
# `demo.lambda_entry`/`demo.app` import keeps the zip small and avoids shipping a
# script that itself has an unused top-level `import boto3` into an environment that
# does not carry boto3.
DEMO_FILES = ["__init__.py", "app.py", "lambda_entry.py", "queries.py"]
DEMO_DIRS = ["static"]

# mangum has no third-party dependencies of its own; fastapi pulls in starlette,
# pydantic, pydantic-core, anyio, sniffio, idna, typing-extensions and annotated-types
# transitively. Listed explicitly anyway (task requirement, and it documents intent
# even though pip would resolve them regardless).
PACKAGE_DEPENDENCIES = [
    "mangum>=0.19",
    "fastapi>=0.110",
    "starlette",
    "pydantic",
    "psycopg[binary]>=3.2",
]

# Same limits as deploy_lambda.py -- see that file's module docstring for the source.
DIRECT_UPLOAD_LIMIT_BYTES = 50 * 1024 * 1024
UNZIPPED_LIMIT_BYTES = 250 * 1024 * 1024

PLATFORM_TAGS = {
    "x86_64": "manylinux2014_x86_64",
    "arm64": "manylinux2014_aarch64",
}


class DeployError(RuntimeError):
    """Raised for problems this script can explain better than a bare traceback."""


# --------------------------------------------------------------------------- package


def cmd_package(args: argparse.Namespace) -> None:
    if args.arch not in PLATFORM_TAGS:
        raise DeployError(f"--arch must be one of {sorted(PLATFORM_TAGS)}, got {args.arch!r}")

    cert_source = args.cert_source or os.environ.get("ROSHAMBO_DEMO_TLS_ROOT_CERT")
    if not cert_source:
        raise DeployError(
            "no TLS root certificate given. Pass --cert-source <path to root.crt> or set "
            "ROSHAMBO_DEMO_TLS_ROOT_CERT -- see this file's module docstring, "
            "'TLS certificate decision', for why the demo needs it bundled."
        )
    cert_path = Path(cert_source)
    if not cert_path.is_file():
        raise DeployError(f"--cert-source {cert_path} does not exist or is not a file")

    output_path = Path(args.output) if args.output else DEFAULT_ZIP_PATH
    build_dir = BUILD_DIR / "demo_package"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    platform_tag = PLATFORM_TAGS[args.arch]
    print(
        f"Downloading Lambda-compatible wheels ({platform_tag}, "
        f"python {args.python_version}) for: {', '.join(PACKAGE_DEPENDENCIES)}"
    )
    pip_cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--platform",
        platform_tag,
        "--target",
        str(build_dir),
        "--implementation",
        "cp",
        "--python-version",
        args.python_version,
        "--only-binary=:all:",
        "--no-compile",
        *PACKAGE_DEPENDENCIES,
    ]
    result = subprocess.run(pip_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise DeployError(
            "pip install for Lambda-target wheels failed (see infra/README.md for the "
            "cross-platform pip flags this relies on):\n"
            f"{result.stdout}\n{result.stderr}"
        )
    print(result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "pip: ok")

    print(f"Copying {DEMO_DIR} -> {build_dir / 'demo'}")
    _copy_demo_tree(DEMO_DIR, build_dir / "demo")

    print(f"Copying {SRC_DIR} -> {build_dir / 'src' / 'roshambo'}")
    _copy_source_tree(SRC_DIR, build_dir / "src" / "roshambo")

    print(f"Copying {ASSETS_DIR} -> {build_dir / 'assets'}")
    _copy_source_tree(ASSETS_DIR, build_dir / "assets")

    dest_cert = build_dir / "certs" / "root.crt"
    dest_cert.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cert_path, dest_cert)
    print(f"Copied TLS root certificate {cert_path} -> {dest_cert}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    _zip_directory(build_dir, output_path)

    size = output_path.stat().st_size
    print(f"Wrote {output_path} ({size / 1024 / 1024:.1f} MiB)")
    if size > UNZIPPED_LIMIT_BYTES:
        raise DeployError(
            f"package is {size} bytes, over Lambda's 250 MiB unzipped limit -- "
            "trim dependencies or split into a layer"
        )
    if size > DIRECT_UPLOAD_LIMIT_BYTES:
        print(
            "WARNING: package exceeds the 50 MiB direct-upload limit. `deploy` will need "
            "an S3 staging bucket for this zip; that path is not implemented in this "
            "script (see infra/README.md)."
        )


def _copy_demo_tree(src: Path, dst: Path) -> None:
    """Copy only the files/dirs the running app imports -- see ``DEMO_FILES``/``DEMO_DIRS``."""
    dst.mkdir(parents=True, exist_ok=True)
    for name in DEMO_FILES:
        item = src / name
        if item.is_file():
            shutil.copy2(item, dst / name)
    for name in DEMO_DIRS:
        item = src / name
        if item.is_dir():
            _copy_source_tree(item, dst / name)


def _copy_source_tree(src: Path, dst: Path) -> None:
    """Copy ``src`` into ``dst``, skipping ``__pycache__`` and ``.pyc`` files.

    Same AWS guidance as ``deploy_lambda.py``'s identical helper: bytecode compiled on a
    different architecture/Python build than the Lambda execution environment can be
    incompatible.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name == "__pycache__":
            continue
        if item.is_dir():
            _copy_source_tree(item, dst / item.name)
        elif item.suffix != ".pyc":
            shutil.copy2(item, dst / item.name)


def _zip_directory(root: Path, output_path: Path) -> None:
    """Zip the contents of ``root`` at the archive root (Lambda requires this layout).

    ``path.relative_to(root)`` yields a ``WindowsPath`` on this host; verified
    empirically that ``zipfile`` still writes forward-slash archive entry names from
    it (``ZipInfo.from_file`` normalizes the separator), so this does not need an
    explicit ``.as_posix()`` -- noted here because it is exactly the kind of thing that
    looks fine on this OS and silently breaks the unzip on Lambda's Linux runtime if it
    were ever not true.
    """
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            zf.write(path, path.relative_to(root))


# ------------------------------------------------------------------------ create-role


def cmd_create_role(args: argparse.Namespace) -> None:
    boto3 = _import_boto3()
    iam = boto3.client("iam")

    trust_policy = json.loads((INFRA_DIR / "iam_trust_policy.json").read_text(encoding="utf-8"))

    role_name = args.role_name
    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for the roshambo-demo Lambda (least privilege: "
            "own log group only, no Bedrock, no S3 -- the demo only reads CockroachDB "
            "over the network).",
        )
        role_arn = role["Role"]["Arn"]
        print(f"created role {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role = iam.get_role(RoleName=role_name)
        role_arn = role["Role"]["Arn"]
        print(f"role {role_arn} already exists, reusing it")

    iam.attach_role_policy(RoleName=role_name, PolicyArn=BASIC_EXECUTION_POLICY_ARN)
    print(f"attached {BASIC_EXECUTION_POLICY_ARN} to {role_name}")
    print(role_arn)


# ---------------------------------------------------------------------------- deploy


def cmd_deploy(args: argparse.Namespace) -> None:
    env_vars = _demo_environment(cert_runtime_path=args.cert_runtime_path)
    boto3 = _import_boto3()

    zip_path = Path(args.zip) if args.zip else DEFAULT_ZIP_PATH
    if not zip_path.is_file():
        raise DeployError(f"{zip_path} does not exist -- run `package` first")
    zip_bytes = zip_path.read_bytes()
    if len(zip_bytes) > DIRECT_UPLOAD_LIMIT_BYTES:
        raise DeployError(
            f"{zip_path} is {len(zip_bytes)} bytes, over the 50 MiB direct-upload limit. "
            "Upload it to S3 yourself and pass --s3-bucket/--s3-key (not implemented here)."
        )

    lam = boto3.client("lambda")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"

    if args.role_arn:
        role_arn = args.role_arn
    else:
        iam = boto3.client("iam")
        role_arn = iam.get_role(RoleName=args.role_name)["Role"]["Arn"]

    try:
        response = lam.create_function(
            FunctionName=args.function_name,
            Runtime=args.runtime,
            Role=role_arn,
            Handler=HANDLER,
            Code={"ZipFile": zip_bytes},
            Timeout=args.timeout,
            MemorySize=args.memory,
            Environment={"Variables": env_vars},
            Architectures=[args.arch],
            Description="Roshambo hackathon demo web app (read-only view of live claims, "
            "denials, and recall search) behind a Lambda Function URL.",
        )
        print(f"created function {response['FunctionArn']}")
    except lam.exceptions.ResourceConflictException:
        print(f"function {args.function_name} already exists, updating it")
        lam.update_function_code(FunctionName=args.function_name, ZipFile=zip_bytes)
        _wait_for_update_settled(lam, args.function_name)
        lam.update_function_configuration(
            FunctionName=args.function_name,
            Role=role_arn,
            Handler=HANDLER,
            Timeout=args.timeout,
            MemorySize=args.memory,
            Environment={"Variables": env_vars},
        )
        _wait_for_update_settled(lam, args.function_name)
        print(f"updated function code and configuration for {args.function_name}")
    except lam.exceptions.InvalidParameterValueException as exc:
        if "role" in str(exc).lower() and not args.no_retry:
            # Same IAM-propagation gotcha as deploy_lambda.py -- see that file.
            print("role not yet assumable by Lambda (IAM propagation delay) -- retrying in 8s")
            time.sleep(8)
            args.no_retry = True
            cmd_deploy(args)
            return
        raise DeployError(str(exc)) from exc

    try:
        lam.put_function_concurrency(
            FunctionName=args.function_name,
            ReservedConcurrentExecutions=args.reserved_concurrency,
        )
        print(
            f"set reserved concurrency to {args.reserved_concurrency} "
            "(hard cap on concurrent invocations -- see module docstring, 'Cost guard')"
        )
    except lam.exceptions.InvalidParameterValueException as exc:
        # AWS reserves a minimum of 10 unreserved concurrent executions across the whole
        # account at all times. A brand-new/low-usage account's own account-level
        # ConcurrentExecutions limit can be as low as 10 (get-account-settings), which
        # makes ANY nonzero ReservedConcurrentExecutions here mathematically impossible
        # without an AWS support limit-increase request -- not something this script can
        # do for you. Not fatal: the account's own low total limit is itself a cost
        # guard at least as strict as the one this flag would have added.
        print(
            f"WARNING: could not set reserved concurrency ({exc}). Likely cause: the "
            "account's own Lambda ConcurrentExecutions limit is at or near AWS's "
            "mandatory 10-execution unreserved floor (check: aws lambda "
            "get-account-settings). The account-wide limit still caps concurrent "
            "invocations of this function, just without a per-function reservation -- "
            "request an AWS service quota increase for "
            "'Concurrent executions' if a firmer per-function cap is needed."
        )
    print(f"region: {region}")


def _wait_for_update_settled(lam: Any, function_name: str, *, attempts: int = 10) -> None:
    """Same rationale as ``deploy_lambda.py``'s identical helper: Lambda serializes
    config/code updates, and a second update while the first is `InProgress` is rejected.
    """
    for _ in range(attempts):
        state = lam.get_function_configuration(FunctionName=function_name)
        if state.get("LastUpdateStatus") != "InProgress":
            return
        time.sleep(2)


def _demo_environment(*, cert_runtime_path: str) -> dict[str, str]:
    """Lambda environment variables for the demo function.

    Reads ``ROSHAMBO_DSN`` from the deploying shell's own environment (never embedded in
    this file) and rewrites its ``sslrootcert`` to point at the certificate bundled into
    the deployment package at ``cert_runtime_path`` -- see the module docstring, "TLS
    certificate decision".
    """
    dsn = os.environ.get("ROSHAMBO_DSN")
    if not dsn:
        raise DeployError(
            "ROSHAMBO_DSN is not set in this shell's environment -- export it before "
            "running `deploy` (see demo/README.md's 'Run it' section for the shape). "
            "It is never read from a file by this script."
        )
    dsn = _ensure_sslrootcert(dsn, cert_runtime_path)

    env_vars = {
        "ROSHAMBO_DSN": dsn,
        "ROSHAMBO_SWARM_ID": os.environ.get("ROSHAMBO_SWARM_ID") or "demo",
        "ROSHAMBO_EMBEDDING_PROVIDER": os.environ.get("ROSHAMBO_EMBEDDING_PROVIDER")
        or "placeholder",
    }
    # Optional passthrough, only set if the deploying shell has them -- same pattern as
    # deploy_lambda.py's _worker_environment.
    for name in ("ROSHAMBO_LEASE_TTL_SECONDS", "ROSHAMBO_EMBEDDING_DIM", "ROSHAMBO_LOG_LEVEL"):
        value = os.environ.get(name)
        if value:
            env_vars[name] = value
    return env_vars


def _ensure_sslrootcert(dsn: str, cert_runtime_path: str) -> str:
    """Point ``sslrootcert`` at the certificate bundled into the deployment package.

    Only touches the DSN when ``sslmode`` requires certificate verification
    (``verify-full``/``verify-ca``) and no ``sslrootcert`` is already present -- a DSN
    that already specifies one, or that uses ``sslmode=disable``/``require`` (no
    verification), is left alone.
    """
    if "sslrootcert=" in dsn:
        return dsn
    if "sslmode=verify-full" not in dsn and "sslmode=verify-ca" not in dsn:
        return dsn
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}sslrootcert={cert_runtime_path}"


# -------------------------------------------------------------------------- enable-url


def cmd_enable_url(args: argparse.Namespace) -> None:
    boto3 = _import_boto3()
    lam = boto3.client("lambda")

    try:
        response = lam.create_function_url_config(
            FunctionName=args.function_name,
            AuthType="NONE",
        )
        function_url = response["FunctionUrl"]
        print(f"created Function URL: {function_url}")
    except lam.exceptions.ResourceConflictException:
        response = lam.get_function_url_config(FunctionName=args.function_name)
        function_url = response["FunctionUrl"]
        print(f"Function URL already exists: {function_url}")

    # AuthType=NONE alone still returns 403 to anonymous callers -- it only means "no
    # IAM SigV4 required", not "public". Public invocation additionally needs BOTH
    # resource-based policy statements below. Since October 2025, AWS requires both
    # lambda:InvokeFunctionUrl AND lambda:InvokeFunction on new function URLs -- a
    # function URL with only the first (the pre-October-2025 shape, and what a naive
    # single add-permission call produces) returns exactly this 403
    # {"Message":"Forbidden...urls-auth.html"} with no other symptom. Confirmed against
    # docs.aws.amazon.com/lambda/latest/dg/urls-auth.html, "Using the NONE auth type",
    # 2026-07-30 -- not guessed.
    try:
        lam.add_permission(
            FunctionName=args.function_name,
            StatementId="FunctionURLAllowPublicAccess",
            Action="lambda:InvokeFunctionUrl",
            Principal="*",
            FunctionUrlAuthType="NONE",
        )
        print("added public invoke permission (FunctionURLAllowPublicAccess)")
    except lam.exceptions.ResourceConflictException:
        print("public invoke permission (InvokeFunctionUrl) already present")
    try:
        lam.add_permission(
            FunctionName=args.function_name,
            StatementId="FunctionURLInvokeAllowPublicAccess",
            Action="lambda:InvokeFunction",
            Principal="*",
            InvokedViaFunctionUrl=True,
        )
        print("added public invoke permission (FunctionURLInvokeAllowPublicAccess)")
    except lam.exceptions.ResourceConflictException:
        print("public invoke permission (InvokeFunction) already present")

    print(function_url)


# -------------------------------------------------------------------------- teardown


def cmd_teardown(args: argparse.Namespace) -> None:
    boto3 = _import_boto3()
    lam = boto3.client("lambda")
    iam = boto3.client("iam")
    logs = boto3.client("logs")

    try:
        lam.delete_function_url_config(FunctionName=args.function_name)
        print(f"deleted Function URL config for {args.function_name}")
    except lam.exceptions.ResourceNotFoundException:
        print(f"Function URL config for {args.function_name} already gone")

    try:
        lam.delete_function(FunctionName=args.function_name)
        print(f"deleted function {args.function_name}")
    except lam.exceptions.ResourceNotFoundException:
        print(f"function {args.function_name} already gone")

    if not args.keep_role:
        try:
            iam.detach_role_policy(RoleName=args.role_name, PolicyArn=BASIC_EXECUTION_POLICY_ARN)
        except iam.exceptions.NoSuchEntityException:
            pass
        try:
            iam.delete_role(RoleName=args.role_name)
            print(f"deleted role {args.role_name}")
        except iam.exceptions.NoSuchEntityException:
            print(f"role {args.role_name} already gone")

    if not args.keep_logs:
        log_group = f"/aws/lambda/{args.function_name}"
        try:
            logs.delete_log_group(logGroupName=log_group)
            print(f"deleted log group {log_group}")
        except logs.exceptions.ResourceNotFoundException:
            print(f"log group {log_group} already gone")

    print(
        "teardown is best-effort, not a transactional stack deletion -- verify in the "
        "console that no roshambo-demo resources remain billable (see infra/README.md)"
    )


# ----------------------------------------------------------------------------- shared


def _import_boto3() -> Any:
    try:
        import boto3
    except ImportError as exc:
        raise DeployError(
            "this subcommand needs boto3. Install with: pip install 'roshambo[aws]' "
            "or: pip install boto3"
        ) from exc
    return boto3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Package, deploy, and tear down the roshambo-demo Lambda Function URL."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_package = sub.add_parser("package", help="build the deployment zip")
    p_package.add_argument("--output", help=f"zip output path (default: {DEFAULT_ZIP_PATH})")
    p_package.add_argument("--python-version", default="3.12")
    p_package.add_argument("--arch", default="x86_64", choices=sorted(PLATFORM_TAGS))
    p_package.add_argument(
        "--cert-source",
        help="path to the CockroachDB TLS root certificate to bundle "
        "(or set ROSHAMBO_DEMO_TLS_ROOT_CERT)",
    )
    p_package.set_defaults(func=cmd_package)

    p_role = sub.add_parser("create-role", help="create/update the least-privilege execution role")
    p_role.add_argument("--role-name", default=ROLE_NAME)
    p_role.set_defaults(func=cmd_create_role)

    p_deploy = sub.add_parser("deploy", help="create/update the Lambda function")
    p_deploy.add_argument("--zip", help=f"path to the packaged zip (default: {DEFAULT_ZIP_PATH})")
    p_deploy.add_argument("--function-name", default=FUNCTION_NAME)
    p_deploy.add_argument("--role-name", default=ROLE_NAME, help="looked up if --role-arn is unset")
    p_deploy.add_argument("--role-arn", help="skip the IAM lookup, use this role ARN directly")
    p_deploy.add_argument("--runtime", default="python3.12")
    p_deploy.add_argument("--arch", default="x86_64", choices=sorted(PLATFORM_TAGS))
    p_deploy.add_argument("--memory", type=int, default=512)
    p_deploy.add_argument("--timeout", type=int, default=15)
    p_deploy.add_argument("--reserved-concurrency", type=int, default=5)
    p_deploy.add_argument(
        "--cert-runtime-path",
        default=f"{LAMBDA_TASK_ROOT}/{CERT_ARCNAME}",
        help="path the packaged certificate will have inside the running function",
    )
    p_deploy.add_argument("--no-retry", action="store_true", help=argparse.SUPPRESS)
    p_deploy.set_defaults(func=cmd_deploy)

    p_url = sub.add_parser("enable-url", help="create the public Function URL")
    p_url.add_argument("--function-name", default=FUNCTION_NAME)
    p_url.set_defaults(func=cmd_enable_url)

    p_teardown = sub.add_parser("teardown", help="best-effort delete of function/role/logs")
    p_teardown.add_argument("--function-name", default=FUNCTION_NAME)
    p_teardown.add_argument("--role-name", default=ROLE_NAME)
    p_teardown.add_argument("--keep-role", action="store_true")
    p_teardown.add_argument("--keep-logs", action="store_true")
    p_teardown.set_defaults(func=cmd_teardown)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except DeployError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # boto3 ClientError etc: fail clearly, not with a raw traceback
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
