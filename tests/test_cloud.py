"""Cloud URI round-trip against a ThreadedMotoServer S3 endpoint.

Every test in this module is skipped if the optional cloud deps
(moto, flask, s3fs, boto3) are missing. Mirrors the three hot paths:

  * ``mpgo_register_file`` hashes an s3:// object and extracts metadata
  * Catalog row survives a fresh session (canonical URI persists)
  * ``mpgo_get_spectrum`` re-opens the same s3:// object lazily
"""
from __future__ import annotations

import asyncio

import pytest

from tests._cloud import s3_fsspec_kwargs, upload_fixture
from tests._fixtures import build_ms_fixture


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def s3_ms_fixture(tmp_path, moto_s3_server):
    endpoint, bucket = moto_s3_server
    local = build_ms_fixture(tmp_path / "cloud.mpgo")
    uri = upload_fixture(endpoint, bucket, "samples/cloud.mpgo", local)
    return endpoint, bucket, uri, local


def test_register_s3_roundtrip(session, s3_ms_fixture):
    from mpeg_o_mcp.tools.register import handle as handle_register

    endpoint, _bucket, uri, _local = s3_ms_fixture
    kwargs = s3_fsspec_kwargs(endpoint)

    result = _run(
        handle_register(
            session,
            {"uri": uri, "fsspec_kwargs": kwargs},
        )
    )

    assert result["uri"] == uri
    assert len(result["file_sha256"]) == 64
    assert result["counts"]["runs"] == 1
    assert result["counts"]["identifications"] == 2
    assert result["counts"]["quantifications"] == 2
    assert result["was_update"] is False


def test_register_s3_hash_matches_local(session, s3_ms_fixture):
    """Cloud and local hashing must agree byte-for-byte."""
    from mpeg_o_mcp.hashes import hash_file_sha256
    from mpeg_o_mcp.tools.register import handle as handle_register

    endpoint, _bucket, uri, local = s3_ms_fixture
    kwargs = s3_fsspec_kwargs(endpoint)

    result = _run(
        handle_register(
            session,
            {"uri": uri, "fsspec_kwargs": kwargs},
        )
    )
    local_sha = hash_file_sha256(local)
    assert result["file_sha256"] == local_sha


def test_get_file_after_s3_register(session, s3_ms_fixture):
    from mpeg_o_mcp.tools.get_file import handle as handle_get
    from mpeg_o_mcp.tools.register import handle as handle_register

    endpoint, _bucket, uri, _local = s3_ms_fixture
    kwargs = s3_fsspec_kwargs(endpoint)

    reg = _run(handle_register(session, {"uri": uri, "fsspec_kwargs": kwargs}))

    got = _run(handle_get(session, {"id": reg["file_id"]}))
    assert got["uri"] == uri
    assert got["counts"]["runs"] == 1

    got_by_uri = _run(handle_get(session, {"uri": uri}))
    assert got_by_uri["id"] == reg["file_id"]


def test_get_spectrum_streams_from_s3(session, empty_keyring, s3_ms_fixture):
    from mpeg_o_mcp.tools.get_spectrum import handle as handle_get_spec
    from mpeg_o_mcp.tools.register import handle as handle_register

    endpoint, _bucket, uri, _local = s3_ms_fixture
    kwargs = s3_fsspec_kwargs(endpoint)

    reg = _run(handle_register(session, {"uri": uri, "fsspec_kwargs": kwargs}))

    spec = _run(
        handle_get_spec(
            session,
            {
                "file_id": reg["file_id"],
                "run_name": "run_0001",
                "spectrum_index": 0,
                "fsspec_kwargs": kwargs,
            },
            keyring=empty_keyring,
        )
    )
    assert spec["spectrum_index"] == 0
    assert "mz" in spec["channels"]
    assert "intensity" in spec["channels"]
    assert len(spec["channels"]["mz"]) > 0


def test_env_default_fsspec_kwargs(monkeypatch, session, s3_ms_fixture):
    """If MPGO_MCP_FSSPEC_KWARGS is set, per-call kwargs may be empty."""
    import json

    from mpeg_o_mcp.tools.register import handle as handle_register

    endpoint, _bucket, uri, _local = s3_ms_fixture
    monkeypatch.setenv(
        "MPGO_MCP_FSSPEC_KWARGS", json.dumps(s3_fsspec_kwargs(endpoint))
    )

    result = _run(handle_register(session, {"uri": uri}))
    assert result["uri"] == uri
    assert result["counts"]["runs"] == 1


def test_per_call_overrides_env_default(monkeypatch, session, s3_ms_fixture):
    """Per-call fsspec_kwargs wins on key-by-key basis."""
    import json

    from mpeg_o_mcp.tools.register import handle as handle_register

    endpoint, _bucket, uri, _local = s3_ms_fixture

    # Env has bogus endpoint; per-call provides the real one.
    bogus = dict(s3_fsspec_kwargs(endpoint))
    bogus["client_kwargs"] = {
        "endpoint_url": "http://127.0.0.1:1",
        "region_name": "us-east-1",
    }
    monkeypatch.setenv("MPGO_MCP_FSSPEC_KWARGS", json.dumps(bogus))

    result = _run(
        handle_register(
            session,
            {"uri": uri, "fsspec_kwargs": s3_fsspec_kwargs(endpoint)},
        )
    )
    assert result["uri"] == uri
