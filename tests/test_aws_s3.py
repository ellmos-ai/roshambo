"""Tests for roshambo.aws.s3 -- mocked boto3, no network, no credentials needed."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from roshambo.aws.s3 import S3NotConfiguredError, get_artifact, parse_s3_uri, put_artifact
from roshambo.config import RoshamboConfig


def _cfg(**overrides) -> RoshamboConfig:
    defaults = dict(
        dsn="postgresql://unused@localhost:1/none",
        swarm_id="test-swarm",
        s3_bucket="roshambo-test-bucket",
    )
    defaults.update(overrides)
    return RoshamboConfig(**defaults)


def test_put_artifact_returns_s3_uri_and_calls_put_object():
    with patch("boto3.client") as mock_client_factory:
        mock_s3 = MagicMock()
        mock_client_factory.return_value = mock_s3

        uri = put_artifact(_cfg(), "trails/abc123.txt", b"hello world", "text/plain")

    assert uri == "s3://roshambo-test-bucket/trails/abc123.txt"
    mock_client_factory.assert_called_once_with("s3", region_name="us-east-1")
    mock_s3.put_object.assert_called_once_with(
        Bucket="roshambo-test-bucket",
        Key="trails/abc123.txt",
        Body=b"hello world",
        ContentType="text/plain",
    )


def test_put_artifact_strips_leading_slash_from_key():
    with patch("boto3.client") as mock_client_factory:
        mock_s3 = MagicMock()
        mock_client_factory.return_value = mock_s3
        uri = put_artifact(_cfg(), "/leading/slash.txt", b"x", "text/plain")
    assert uri == "s3://roshambo-test-bucket/leading/slash.txt"


def test_put_artifact_without_bucket_configured_raises_clearly():
    with pytest.raises(S3NotConfiguredError, match="ROSHAMBO_S3_BUCKET"):
        put_artifact(_cfg(s3_bucket=None), "key.txt", b"x", "text/plain")


def test_get_artifact_from_full_uri():
    with patch("boto3.client") as mock_client_factory:
        mock_s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b"payload bytes"
        mock_s3.get_object.return_value = {"Body": body}
        mock_client_factory.return_value = mock_s3

        data = get_artifact(_cfg(), "s3://other-bucket/some/key.bin")

    assert data == b"payload bytes"
    mock_s3.get_object.assert_called_once_with(Bucket="other-bucket", Key="some/key.bin")


def test_get_artifact_from_bare_key_uses_configured_bucket():
    with patch("boto3.client") as mock_client_factory:
        mock_s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b"payload"
        mock_s3.get_object.return_value = {"Body": body}
        mock_client_factory.return_value = mock_s3

        get_artifact(_cfg(), "bare/key.bin")

    mock_s3.get_object.assert_called_once_with(Bucket="roshambo-test-bucket", Key="bare/key.bin")


def test_get_artifact_bare_key_without_bucket_configured_raises():
    with pytest.raises(S3NotConfiguredError):
        get_artifact(_cfg(s3_bucket=None), "bare/key.bin")


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("s3://bucket/key", ("bucket", "key")),
        ("s3://bucket/nested/path/key.txt", ("bucket", "nested/path/key.txt")),
    ],
)
def test_parse_s3_uri(uri, expected):
    assert parse_s3_uri(uri) == expected


@pytest.mark.parametrize(
    "bad_uri",
    ["https://bucket/key", "s3://bucket-without-key", "s3://", "not-a-uri"],
)
def test_parse_s3_uri_rejects_malformed_input(bad_uri):
    with pytest.raises(ValueError):
        parse_s3_uri(bad_uri)
