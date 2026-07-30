#!/usr/bin/env python3
"""Package, deploy, invoke, and tear down the ``roshambo-worker`` Lambda function.

Plain boto3 + zipfile, deliberately not AWS SAM -- see ``infra/README.md`` for why.

Subcommands
-----------

``package``         Build ``roshambo-worker.zip``: ``src/roshambo`` plus the third-party
                     dependencies it needs at runtime (boto3, botocore, psycopg[binary]),
                     downloaded as Lambda-compatible (manylinux) wheels regardless of the
                     platform this script runs on.
``create-role``      Create or update the IAM execution role from
                     ``iam_trust_policy.json`` + ``iam_execution_policy.json``.
``deploy``           Create or update the Lambda function itself from a packaged zip.
``invoke``           Invoke the deployed function with a JSON event and print the result.
``teardown``         Best-effort delete of the function, its role, and its log group.

None of this has been run against a real AWS account from this environment -- there are
no AWS credentials available here (verified, see ``docs/EVIDENCE-aws.md``). ``package`` is
the exception: it touches no AWS API at all (it only downloads wheels from PyPI and builds
a local zip), so it has been exercised for real; see EVIDENCE-aws.md for the output.

Why bundle boto3/botocore ourselves instead of relying on the version built into the
Lambda Python runtime: AWS's own docs for .zip deployment packages recommend exactly this
("To maintain full control over your dependencies and to avoid possible version
misalignment issues, we recommend you add all of your function's dependencies to your
deployment package, even if versions of them are included in the Lambda runtime. This
includes the Boto3 SDK.", docs.aws.amazon.com/lambda/latest/dg/python-package.html,
section "Runtime dependencies in Python", checked 2026-07-25). Concretely, this worker
calls Bedrock's Converse API and Titan V2 embeddings; pinning our own boto3/botocore
avoids depending on whichever SDK snapshot a given Lambda runtime happened to freeze.
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
SRC_DIR = REPO_ROOT / "src" / "roshambo"
INFRA_DIR = Path(__file__).resolve().parent
BUILD_DIR = INFRA_DIR / "build"
DEFAULT_ZIP_PATH = BUILD_DIR / "roshambo-worker.zip"

FUNCTION_NAME = "roshambo-worker"
ROLE_NAME = "roshambo-worker-role"
HANDLER = "roshambo.aws.worker.lambda_handler"

# Runtime dependencies that are NOT pure Python and/or that we deliberately pin
# ourselves rather than trust to the Lambda runtime's built-in version (see module
# docstring). Mirrors pyproject.toml's core dependency (psycopg[binary]) plus the
# `aws` extra (boto3; botocore comes along transitively but is pinned explicitly
# here too since it is the package that actually changes Bedrock/Lambda API shapes).
PACKAGE_DEPENDENCIES = ["boto3>=1.35", "botocore>=1.35", "psycopg[binary]>=3.2"]

# Direct .zip upload to Lambda is capped at 50 MB; above that the zip must go via S3
# first (not implemented here -- out of scope for a single-function hackathon
# deployment). Unzipped size is capped at 250 MB regardless of upload method.
# Source: docs.aws.amazon.com/lambda/latest/dg/python-package.html, checked 2026-07-25.
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
    if shutil.which(sys.executable) is None:  # pragma: no cover - defensive only
        raise DeployError("no usable Python interpreter found to run pip with")
    if args.arch not in PLATFORM_TAGS:
        raise DeployError(f"--arch must be one of {sorted(PLATFORM_TAGS)}, got {args.arch!r}")

    output_path = Path(args.output) if args.output else DEFAULT_ZIP_PATH
    build_dir = BUILD_DIR / "package"
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
        "--no-compile",  # don't generate __pycache__ -- see _zip_directory's docstring
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

    print(f"Copying {SRC_DIR} -> {build_dir / 'roshambo'}")
    _copy_source_tree(SRC_DIR, build_dir / "roshambo")

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
            "WARNING: package exceeds the 50 MiB direct-upload limit. "
            "`deploy` will need an S3 staging bucket for this zip; that path is not "
            "implemented in this script (see infra/README.md). Trim dependencies, or "
            "upload the zip to S3 yourself and pass --s3-bucket/--s3-key to `deploy`."
        )


def _copy_source_tree(src: Path, dst: Path) -> None:
    """Copy ``src`` into ``dst``, skipping ``__pycache__`` and ``.pyc`` files.

    AWS's own docs warn against shipping ``__pycache__``: bytecode compiled on a
    different architecture or Python build than the Lambda execution environment can be
    incompatible (docs.aws.amazon.com/lambda/latest/dg/python-package.html, "Using
    __pycache__ folders", checked 2026-07-25).
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

    Excludes ``__pycache__``/``.pyc`` regardless of where they came from -- belt and
    suspenders alongside ``--no-compile`` on the pip install and the same skip in
    ``_copy_source_tree``: pip's install step still byte-compiles pure-Python
    dependencies with *this* interpreter unless told not to, and that bytecode is not a
    thing we want to assert is compatible with the Lambda execution environment (see
    ``_copy_source_tree``'s docstring for the AWS guidance this follows).
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
    sts = boto3.client("sts")

    account_id = sts.get_caller_identity()["Account"]
    region = _env("AWS_REGION", "eu-central-1")
    # Deliberately separate from `region`: Bedrock model access is granted per region
    # and this account's eu-central-1 Titan quota is 0, while us-east-2 has it --
    # see roshambo.config.DEFAULT_BEDROCK_REGION's docstring. The IAM policy's Bedrock
    # ARNs must name the region the calls actually go to, or InvokeModel fails with
    # AccessDenied even though the role looks correctly scoped.
    bedrock_region = _env("BEDROCK_REGION", "us-east-2")
    worker_model_id = _env("WORKER_BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
    embed_model_id = _env("BEDROCK_MODEL_ID", "amazon.titan-embed-text-v2:0")
    s3_bucket = _env("S3_BUCKET", None)
    if not s3_bucket:
        raise DeployError(
            "ROSHAMBO_S3_BUCKET must be set (the execution policy scopes s3:PutObject/"
            "GetObject to exactly this bucket -- no wildcard resource)"
        )

    trust_policy = json.loads((INFRA_DIR / "iam_trust_policy.json").read_text(encoding="utf-8"))

    execution_policy_text = (INFRA_DIR / "iam_execution_policy.json").read_text(encoding="utf-8")
    execution_policy_text = (
        execution_policy_text.replace("{{AWS_REGION}}", region)
        .replace("{{BEDROCK_REGION}}", bedrock_region)
        .replace("{{AWS_ACCOUNT_ID}}", account_id)
        .replace("{{WORKER_BEDROCK_MODEL_ID}}", worker_model_id)
        .replace("{{S3_BUCKET_NAME}}", s3_bucket)
    )
    # Two model ARNs are baked into the policy template (the worker model AND the
    # embedding model); the embed model ARN is filled by a second, narrower pass so
    # create-role stays a single templating step instead of a second JSON edit.
    execution_policy_text = execution_policy_text.replace(
        f"foundation-model/{worker_model_id}",
        f"foundation-model/{worker_model_id}",
    )
    execution_policy = json.loads(execution_policy_text)
    # Fix up the embedding-model ARN, which the template hardcodes as the Titan
    # default -- replace it if a different embedding model was configured.
    for statement in execution_policy["Statement"]:
        if statement.get("Sid") == "InvokeBedrockModels":
            resources = statement["Resource"]
            embed_arn = f"arn:aws:bedrock:{bedrock_region}::foundation-model/{embed_model_id}"
            statement["Resource"] = [embed_arn if "titan-embed" in r else r for r in resources]

    role_name = args.role_name
    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for the roshambo-worker Lambda (least privilege: "
            "own log group, two Bedrock model ARNs, one S3 bucket).",
        )
        role_arn = role["Role"]["Arn"]
        print(f"created role {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role = iam.get_role(RoleName=role_name)
        role_arn = role["Role"]["Arn"]
        print(f"role {role_arn} already exists, reusing it")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="roshambo-worker-execution-policy",
        PolicyDocument=json.dumps(execution_policy),
    )
    print(f"attached/updated inline policy on {role_name}")
    print(role_arn)


# ---------------------------------------------------------------------------- deploy


def cmd_deploy(args: argparse.Namespace) -> None:
    # Validate our own required config (ROSHAMBO_DSN etc.) before touching any boto3
    # client, so a misconfigured ROSHAMBO_* environment gets our own clear message
    # instead of an unrelated-looking NoRegionError/NoCredentialsError from boto3.
    env_vars = _worker_environment()
    boto3 = _import_boto3()

    zip_path = Path(args.zip) if args.zip else DEFAULT_ZIP_PATH
    if not zip_path.is_file():
        raise DeployError(f"{zip_path} does not exist -- run `package` first")
    zip_bytes = zip_path.read_bytes()
    if len(zip_bytes) > DIRECT_UPLOAD_LIMIT_BYTES:
        raise DeployError(
            f"{zip_path} is {len(zip_bytes)} bytes, over the 50 MiB direct-upload limit. "
            "Upload it to S3 yourself and pass --s3-bucket/--s3-key (not otherwise "
            "implemented here -- see infra/README.md)."
        )

    lam = boto3.client("lambda")
    region = _env("AWS_REGION", "eu-central-1")

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
            Description="Roshambo autonomous worker: claims a resource, checks Roshambo's "
            "memory for prior attempts, does a Bedrock Claude call, records the outcome.",
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
        print(f"updated function code and configuration for {args.function_name}")
    except lam.exceptions.InvalidParameterValueException as exc:
        if "role" in str(exc).lower() and not args.no_retry:
            # IAM role creation is eventually consistent; a role that was just created
            # by `create-role` may not be assumable by Lambda for a few seconds yet.
            # This is a well-documented AWS gotcha, not a configuration mistake.
            print("role not yet assumable by Lambda (IAM propagation delay) -- retrying in 8s")
            time.sleep(8)
            args.no_retry = True  # only one retry
            cmd_deploy(args)
            return
        raise DeployError(str(exc)) from exc

    print(f"region: {region}")


def _wait_for_update_settled(lam: Any, function_name: str, *, attempts: int = 10) -> None:
    """Lambda serializes config/code updates -- a second update while the first is
    still `InProgress` is rejected. Poll `LastUpdateStatus` briefly rather than assume.
    """
    for _ in range(attempts):
        state = lam.get_function_configuration(FunctionName=function_name)
        if state.get("LastUpdateStatus") != "InProgress":
            return
        time.sleep(2)


def _worker_environment() -> dict[str, str]:
    """Lambda environment variables, taken from the local ``ROSHAMBO_*`` environment at
    deploy time. ``ROSHAMBO_DSN`` in particular carries the CockroachDB connection string
    (and its embedded credentials, if any) -- Lambda environment variables are encrypted
    at rest but visible to anyone with function-read IAM permissions. Using AWS Secrets
    Manager instead is the production-hardening step; out of scope for this hackathon
    deployment, called out here rather than silently skipped.
    """
    required = ["ROSHAMBO_DSN"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise DeployError(
            f"missing required environment variable(s) for deploy: {', '.join(missing)}"
        )
    passthrough = [
        "ROSHAMBO_DSN",
        "ROSHAMBO_SWARM_ID",
        "ROSHAMBO_EMBEDDING_DIM",
        "ROSHAMBO_LEASE_TTL_SECONDS",
        "ROSHAMBO_EMBEDDING_PROVIDER",
        "ROSHAMBO_AWS_REGION",
        "ROSHAMBO_BEDROCK_REGION",
        "ROSHAMBO_BEDROCK_MODEL_ID",
        "ROSHAMBO_S3_BUCKET",
        "ROSHAMBO_WORKER_BEDROCK_MODEL_ID",
        "ROSHAMBO_LOG_LEVEL",
    ]
    return {name: os.environ[name] for name in passthrough if os.environ.get(name)}


# ---------------------------------------------------------------------------- invoke


def cmd_invoke(args: argparse.Namespace) -> None:
    boto3 = _import_boto3()

    if args.event_json:
        event = json.loads(args.event_json)
    elif args.event:
        event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    else:
        raise DeployError("pass --event <file.json> or --event-json '<json>'")

    lam = boto3.client("lambda")
    response = lam.invoke(
        FunctionName=args.function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(event).encode("utf-8"),
    )
    payload = json.loads(response["Payload"].read())
    print(json.dumps(payload, indent=2, default=str))
    if response.get("FunctionError"):
        raise DeployError(f"Lambda reported a function error: {response['FunctionError']}")


# -------------------------------------------------------------------------- teardown


def cmd_teardown(args: argparse.Namespace) -> None:
    boto3 = _import_boto3()
    lam = boto3.client("lambda")
    iam = boto3.client("iam")
    logs = boto3.client("logs")

    try:
        lam.delete_function(FunctionName=args.function_name)
        print(f"deleted function {args.function_name}")
    except lam.exceptions.ResourceNotFoundException:
        print(f"function {args.function_name} already gone")

    if not args.keep_role:
        try:
            iam.delete_role_policy(
                RoleName=args.role_name, PolicyName="roshambo-worker-execution-policy"
            )
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
        "console that no roshambo-worker resources remain billable (see infra/README.md)"
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


def _env(name: str, default: str | None) -> str | None:
    return os.environ.get(f"ROSHAMBO_{name}", default)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Package, deploy, invoke, and tear down the roshambo-worker Lambda."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_package = sub.add_parser("package", help="build the deployment zip")
    p_package.add_argument("--output", help=f"zip output path (default: {DEFAULT_ZIP_PATH})")
    p_package.add_argument("--python-version", default="3.12")
    p_package.add_argument("--arch", default="x86_64", choices=sorted(PLATFORM_TAGS))
    p_package.set_defaults(func=cmd_package)

    p_role = sub.add_parser("create-role", help="create/update the IAM execution role")
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
    p_deploy.add_argument("--timeout", type=int, default=60)
    p_deploy.add_argument("--no-retry", action="store_true", help=argparse.SUPPRESS)
    p_deploy.set_defaults(func=cmd_deploy)

    p_invoke = sub.add_parser("invoke", help="invoke the deployed function")
    p_invoke.add_argument("--function-name", default=FUNCTION_NAME)
    p_invoke.add_argument("--event", help="path to a JSON event file")
    p_invoke.add_argument("--event-json", help="inline JSON event")
    p_invoke.set_defaults(func=cmd_invoke)

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
