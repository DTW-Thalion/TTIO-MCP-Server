"""M6: ``mpgo_push_file`` round-trip against a ThreadedMotoServer S3 endpoint.

Skipped unless the optional cloud deps (moto, flask, s3fs, boto3) are
present. Covers plaintext push, encrypt-on-push, scheme validation,
local-source-missing, and keyring failure paths.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

import pytest

from mpeg_o_mcp.catalog import ResolveFailed
from mpeg_o_mcp.hashes import hash_file_sha256
from mpeg_o_mcp.keyring import AES_256_GCM, AES_256_GCM_KEY_LEN, KeyNotFound, Keyring
from mpeg_o_mcp.tools.get_file import handle as handle_get_file
from mpeg_o_mcp.tools.get_spectrum import handle as handle_get_spec
from mpeg_o_mcp.tools.push_file import SchemeNotWritable
from mpeg_o_mcp.tools.push_file import handle as handle_push
from tests._cloud import s3_fsspec_kwargs
from tests._fixtures import build_ms_fixture


def _run(coro):
    return asyncio.run(coro)


def _write_keyring(path: Path, key_id: str, raw: bytes) -> Path:
    path.write_text(
        json.dumps(
            {
                "keys": {
                    key_id: {
                        "value": base64.b64encode(raw).decode("ascii"),
                        "algorithm": AES_256_GCM,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def keyring_with_key(tmp_path: Path) -> tuple[Keyring, str, bytes]:
    raw = os.urandom(AES_256_GCM_KEY_LEN)
    kr_path = _write_keyring(tmp_path / "kr.json", "demo", raw)
    return Keyring.from_path(kr_path), "demo", raw


@pytest.fixture
def local_ms(tmp_path: Path) -> Path:
    return build_ms_fixture(tmp_path / "local.mpgo")


def test_push_plaintext_to_s3(session, empty_keyring, moto_s3_server, local_ms):
    endpoint, bucket = moto_s3_server
    kwargs = s3_fsspec_kwargs(endpoint)
    remote_uri = f"s3://{bucket}/push/plain.mpgo"

    result = _run(
        handle_push(
            session,
            {
                "local_uri": str(local_ms),
                "remote_uri": remote_uri,
                "fsspec_kwargs": kwargs,
            },
            keyring=empty_keyring,
        )
    )

    assert result["uri"] == remote_uri
    assert result["remote_uri"] == remote_uri
    assert result["encrypted"] is False
    assert result["encrypted_algorithm"] is None
    assert result["key_id"] is None
    assert result["counts"]["runs"] == 1
    assert result["counts"]["identifications"] == 2

    # Hash on the uploaded object matches the local file byte-for-byte.
    assert result["file_sha256"] == hash_file_sha256(local_ms)

    # Catalog row reflects plaintext.
    got = _run(handle_get_file(session, {"id": result["file_id"]}))
    assert got["encrypted"] is False
    assert got["encrypted_algorithm"] is None


def test_push_encrypted_to_s3(
    session, moto_s3_server, local_ms, keyring_with_key
):
    kr, key_id, _raw = keyring_with_key
    endpoint, bucket = moto_s3_server
    kwargs = s3_fsspec_kwargs(endpoint)
    remote_uri = f"s3://{bucket}/push/encrypted.mpgo"

    local_sha_pre = hash_file_sha256(local_ms)

    result = _run(
        handle_push(
            session,
            {
                "local_uri": str(local_ms),
                "remote_uri": remote_uri,
                "key_id": key_id,
                "fsspec_kwargs": kwargs,
            },
            keyring=kr,
        )
    )

    assert result["encrypted"] is True
    assert result["encrypted_algorithm"] == AES_256_GCM
    assert result["key_id"] == key_id
    # Ciphertext on the wire differs from plaintext on disk.
    assert result["file_sha256"] != local_sha_pre

    # Local source is untouched.
    assert hash_file_sha256(local_ms) == local_sha_pre

    # Catalog row carries the encryption flag.
    got = _run(handle_get_file(session, {"id": result["file_id"]}))
    assert got["encrypted"] is True
    assert got["encrypted_algorithm"] == AES_256_GCM

    # Reading the encrypted spectrum through the remote URI works when the
    # same key_id is supplied — proves the ciphertext is actually a valid
    # MPEG-O-encrypted object.
    spec = _run(
        handle_get_spec(
            session,
            {
                "file_id": result["file_id"],
                "run_name": "run_0001",
                "spectrum_index": 0,
                "key_id": key_id,
                "fsspec_kwargs": kwargs,
            },
            keyring=kr,
        )
    )
    assert len(spec["channels"]["intensity"]) > 0


def test_push_rejects_https_remote(session, empty_keyring, local_ms):
    with pytest.raises(SchemeNotWritable):
        _run(
            handle_push(
                session,
                {
                    "local_uri": str(local_ms),
                    "remote_uri": "https://example.com/x.mpgo",
                },
                keyring=empty_keyring,
            )
        )


def test_push_rejects_file_remote(session, empty_keyring, local_ms, tmp_path):
    with pytest.raises(SchemeNotWritable):
        _run(
            handle_push(
                session,
                {
                    "local_uri": str(local_ms),
                    "remote_uri": f"file://{tmp_path / 'copy.mpgo'}",
                },
                keyring=empty_keyring,
            )
        )


def test_push_missing_local_source(session, empty_keyring, tmp_path, moto_s3_server):
    endpoint, bucket = moto_s3_server
    kwargs = s3_fsspec_kwargs(endpoint)
    missing = tmp_path / "nope.mpgo"

    with pytest.raises(ResolveFailed):
        _run(
            handle_push(
                session,
                {
                    "local_uri": str(missing),
                    "remote_uri": f"s3://{bucket}/push/missing.mpgo",
                    "fsspec_kwargs": kwargs,
                },
                keyring=empty_keyring,
            )
        )


def test_push_unknown_key_id(session, moto_s3_server, local_ms, keyring_with_key):
    kr, _key_id, _raw = keyring_with_key
    endpoint, bucket = moto_s3_server
    kwargs = s3_fsspec_kwargs(endpoint)

    with pytest.raises(KeyNotFound):
        _run(
            handle_push(
                session,
                {
                    "local_uri": str(local_ms),
                    "remote_uri": f"s3://{bucket}/push/bad-key.mpgo",
                    "key_id": "does-not-exist",
                    "fsspec_kwargs": kwargs,
                },
                keyring=kr,
            )
        )
