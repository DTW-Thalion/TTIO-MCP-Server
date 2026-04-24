"""ThreadedMotoServer-backed S3 fixture for cloud-roundtrip tests.

We use ``ThreadedMotoServer`` (a real HTTP server) rather than the
``@mock_aws`` decorator. The decorator patches boto3 at the client
layer, which aiobotocore — the async driver s3fs uses — does not
participate in, so reads ``await`` on a raw ``bytes`` object and blow
up. The threaded server is driver-agnostic and works with both.

Usage::

    from tests._cloud import moto_s3_server, s3_fsspec_kwargs

    def test_cloud(moto_s3_server):
        endpoint, bucket = moto_s3_server
        kwargs = s3_fsspec_kwargs(endpoint)
        ...
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(scope="session")
def moto_s3_server() -> Iterator[tuple[str, str]]:
    """Boot a moto S3 server and create one bucket for the session.

    Yields ``(endpoint_url, bucket_name)``. The bucket is empty on
    yield; individual tests upload their own fixtures.
    """
    pytest.importorskip("moto.server")
    pytest.importorskip("flask")
    pytest.importorskip("s3fs")

    import boto3
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://{host}:{port}"

    bucket = "mpgo-test-bucket"
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    s3.create_bucket(Bucket=bucket)

    try:
        yield endpoint, bucket
    finally:
        server.stop()


def s3_fsspec_kwargs(endpoint: str) -> dict[str, Any]:
    """Build the fsspec kwargs that point s3fs at a moto endpoint."""
    return {
        "key": "testing",
        "secret": "testing",
        "client_kwargs": {"endpoint_url": endpoint, "region_name": "us-east-1"},
    }


def upload_fixture(endpoint: str, bucket: str, key: str, local_path) -> str:
    """Upload a local file to the moto bucket. Returns the s3:// URI."""
    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    s3.upload_file(str(local_path), bucket, key)
    return f"s3://{bucket}/{key}"
