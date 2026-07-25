"""AWS integration for Roshambo: S3 artifact storage and the Lambda worker.

Re-exports the two names frozen in CONTRACT.md under the ``roshambo.aws``
namespace:

    from roshambo.aws import put_artifact, lambda_handler
"""

from roshambo.aws.s3 import get_artifact, put_artifact
from roshambo.aws.worker import lambda_handler

__all__ = ["get_artifact", "lambda_handler", "put_artifact"]
