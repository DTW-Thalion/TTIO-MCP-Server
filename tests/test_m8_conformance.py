"""M8: MCP conformance suite.

Drives the real ``ttio-mcp`` subprocess via the ``mcp`` Python client
SDK over stdio. Every other test suite in this repo calls tool handlers
in-process; M8 proves the server works end-to-end as an MCP server:
JSON-RPC 2.0 over stdio, protocol-version negotiation in the
``initialize`` handshake, ``tools/list`` shape, and ``tools/call``
round-trips through the JSON envelope.

Four tests cover:

- Initialize handshake and ``list_tools`` surface (all 14 tool names,
  every schema is valid JSON Schema).
- Happy path across 12 of the 14 tools on a local MS fixture:
  register → list → get → get_run → search_identifications →
  get_quantifications → get_spectrum → sign → verify_signature →
  reverify → encrypt → get_spectrum (with ``key_id``) → decrypt.
  State carries through one subprocess — the catalog row accumulates
  the real ``signed``/``encrypted`` flag transitions you'd see in
  production.
- ``ttio_push_file`` end-to-end against a ``ThreadedMotoServer`` S3
  endpoint — skipped when the cloud extras aren't installed.
- Structured error envelope — a lookup by bogus id returns
  ``{"ok": false, "error": {"code": "not_found", ...}}`` on the wire.

The subprocess is booted fresh for each test; env vars and a seeded
SQLite catalog live in ``tmp_path`` so tests are hermetic.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ttio_mcp.db import Base, make_engine, make_session_factory
from ttio_mcp.db.models import User
from ttio_mcp.keyring import AES_256_GCM, AES_256_GCM_KEY_LEN, HMAC_SHA256
from tests._fixtures import build_ms_fixture

EXPECTED_TOOLS = {
    "ttio_register_file",
    "ttio_list_files",
    "ttio_get_file",
    "ttio_reverify",
    "ttio_search_identifications",
    "ttio_get_run",
    "ttio_get_spectrum",
    "ttio_get_quantifications",
    "ttio_encrypt_file",
    "ttio_decrypt_file",
    "ttio_push_file",
    "ttio_sign_file",
    "ttio_verify_signature",
    "ttio_launch_uploader",
}


def _seed_db(db_path: Path) -> str:
    """Create all tables and the seeded ``system`` user. Returns the URL."""
    url = f"sqlite:///{db_path}"
    eng = make_engine(url)
    Base.metadata.create_all(eng)
    factory = make_session_factory(eng)
    with factory() as s:
        s.add(User(name="system"))
        s.commit()
    eng.dispose()
    return url


def _write_keyring(path: Path) -> Path:
    """Write a keyring holding one AES-256-GCM key and one HMAC-SHA256 key."""
    aes_raw = os.urandom(AES_256_GCM_KEY_LEN)
    hmac_raw = os.urandom(32)
    path.write_text(
        json.dumps(
            {
                "keys": {
                    "aes-demo": {
                        "value": base64.b64encode(aes_raw).decode("ascii"),
                        "algorithm": AES_256_GCM,
                    },
                    "hmac-demo": {
                        "value": base64.b64encode(hmac_raw).decode("ascii"),
                        "algorithm": HMAC_SHA256,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def _server_params(env_overrides: dict[str, str]) -> StdioServerParameters:
    """Build ``StdioServerParameters`` that launches the real server.

    Prefers the installed ``ttio-mcp`` console script; falls back to
    ``python -m ttio_mcp.server`` so the test works against an
    editable install without relying on ``PATH`` order.
    """
    command = shutil.which("ttio-mcp") or sys.executable
    args: list[str] = []
    if command == sys.executable:
        args = ["-m", "ttio_mcp.server"]

    child_env = os.environ.copy()
    child_env.update(env_overrides)
    return StdioServerParameters(command=command, args=args, env=child_env)


@asynccontextmanager
async def _session(params: StdioServerParameters) -> AsyncIterator[ClientSession]:
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def _call(session: ClientSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Invoke ``name`` with ``args`` and parse the single TextContent envelope."""
    result = await session.call_tool(name, args)
    assert result.content, f"{name}: empty content"
    text = result.content[0].text  # type: ignore[union-attr]
    payload = json.loads(text)
    assert isinstance(payload, dict), f"{name}: envelope is not a dict"
    assert "ok" in payload, f"{name}: envelope missing 'ok'"
    return payload


# ---------------------------------------------------------------------------
# 1. initialize + list_tools surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conformance_initialize_and_list_tools(tmp_path: Path) -> None:
    db_url = _seed_db(tmp_path / "catalog.db")
    params = _server_params({"TTIO_MCP_DB_URL": db_url})

    async with _session(params) as session:
        result = await session.list_tools()

    tool_names = {t.name for t in result.tools}
    assert tool_names == EXPECTED_TOOLS, (
        f"tool surface mismatch: "
        f"missing={EXPECTED_TOOLS - tool_names} extra={tool_names - EXPECTED_TOOLS}"
    )

    # Every schema has to be a valid JSON Schema object with an object root.
    for tool in result.tools:
        schema = tool.inputSchema
        assert isinstance(schema, dict), f"{tool.name}: schema is not dict"
        assert schema.get("type") == "object", f"{tool.name}: schema type != object"
        # additionalProperties is enforced on every tool to reject unknown fields.
        assert schema.get("additionalProperties") is False, (
            f"{tool.name}: schema does not reject unknown fields"
        )
        assert isinstance(schema.get("properties"), dict), (
            f"{tool.name}: schema missing properties"
        )


# ---------------------------------------------------------------------------
# 2. Happy-path round-trip — 12 of 14 tools on a local fixture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conformance_local_tools_round_trip(tmp_path: Path) -> None:
    db_url = _seed_db(tmp_path / "catalog.db")
    keyring_path = _write_keyring(tmp_path / "keyring.json")
    fixture = build_ms_fixture(tmp_path / "sample.mpgo")

    params = _server_params({
        "TTIO_MCP_DB_URL": db_url,
        "TTIO_KEYRING_PATH": str(keyring_path),
    })

    async with _session(params) as session:
        # --- register ---
        r = await _call(session, "ttio_register_file", {"uri": str(fixture)})
        assert r["ok"] is True, r
        file_id = r["data"]["file_id"]
        assert r["data"]["counts"]["runs"] == 1
        assert r["data"]["counts"]["identifications"] == 2
        assert r["data"]["counts"]["quantifications"] == 2

        # --- list ---
        r = await _call(session, "ttio_list_files", {})
        assert r["ok"] is True
        assert r["data"]["total"] == 1
        assert r["data"]["files"][0]["id"] == file_id

        # --- get ---
        r = await _call(session, "ttio_get_file", {"id": file_id})
        assert r["ok"] is True
        assert r["data"]["id"] == file_id
        run_id = r["data"]["runs"][0]["id"]

        # --- get_run ---
        r = await _call(session, "ttio_get_run", {"run_id": run_id})
        assert r["ok"] is True
        assert r["data"]["acquisition_mode"]
        assert len(r["data"]["identifications"]) == 2

        # --- search_identifications ---
        r = await _call(
            session, "ttio_search_identifications", {"chebi_id": "CHEBI:15377"}
        )
        assert r["ok"] is True
        assert r["data"]["total"] == 1
        assert r["data"]["identifications"][0]["chebi_id"] == "CHEBI:15377"

        # --- get_quantifications ---
        r = await _call(session, "ttio_get_quantifications", {"file_id": file_id})
        assert r["ok"] is True
        assert r["data"]["total"] == 2

        # --- get_spectrum (plaintext) ---
        r = await _call(
            session,
            "ttio_get_spectrum",
            {"run_id": run_id, "spectrum_index": 0},
        )
        assert r["ok"] is True
        assert len(r["data"]["channels"]["intensity"]) > 0
        assert len(r["data"]["channels"]["mz"]) > 0

        # --- sign ---
        r = await _call(
            session,
            "ttio_sign_file",
            {"id": file_id, "key_id": "hmac-demo"},
        )
        assert r["ok"] is True
        assert r["data"]["signed"] is True
        assert r["data"]["signature_algorithm"] == HMAC_SHA256
        assert r["data"]["signed_dataset_count"] >= 1

        # --- verify_signature ---
        r = await _call(
            session,
            "ttio_verify_signature",
            {"id": file_id, "key_id": "hmac-demo"},
        )
        assert r["ok"] is True
        assert r["data"]["valid"] is True
        assert all(r["data"]["verified_datasets"].values())

        # --- reverify (no drift — signing updated catalog hashes) ---
        r = await _call(session, "ttio_reverify", {"id": file_id})
        assert r["ok"] is True
        assert r["data"]["resolved"] is True
        assert r["data"]["drift"] is False

        # --- encrypt ---
        r = await _call(
            session,
            "ttio_encrypt_file",
            {"id": file_id, "key_id": "aes-demo"},
        )
        assert r["ok"] is True
        assert r["data"]["encrypted"] is True
        assert r["data"]["encrypted_algorithm"] == AES_256_GCM

        # --- get_spectrum (with key_id, against encrypted file) ---
        r = await _call(
            session,
            "ttio_get_spectrum",
            {"run_id": run_id, "spectrum_index": 0, "key_id": "aes-demo"},
        )
        assert r["ok"] is True
        assert len(r["data"]["channels"]["intensity"]) > 0

        # --- decrypt ---
        r = await _call(
            session,
            "ttio_decrypt_file",
            {"id": file_id, "key_id": "aes-demo"},
        )
        assert r["ok"] is True
        assert r["data"]["encrypted"] is False


# ---------------------------------------------------------------------------
# 3. ttio_push_file — cloud path, skipped without moto
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conformance_push_file(tmp_path: Path, moto_s3_server) -> None:
    pytest.importorskip("s3fs")
    from tests._cloud import s3_fsspec_kwargs

    endpoint, bucket = moto_s3_server
    db_url = _seed_db(tmp_path / "catalog.db")
    fixture = build_ms_fixture(tmp_path / "local.mpgo")
    remote_uri = f"s3://{bucket}/conformance/push.mpgo"

    fsspec_kwargs = s3_fsspec_kwargs(endpoint)
    params = _server_params({
        "TTIO_MCP_DB_URL": db_url,
        "TTIO_MCP_FSSPEC_KWARGS": json.dumps(fsspec_kwargs),
    })

    async with _session(params) as session:
        r = await _call(
            session,
            "ttio_push_file",
            {"local_uri": str(fixture), "remote_uri": remote_uri},
        )

    assert r["ok"] is True, r
    assert r["data"]["uri"] == remote_uri
    assert r["data"]["encrypted"] is False
    assert r["data"]["counts"]["runs"] == 1


# ---------------------------------------------------------------------------
# 4. Error envelope on the wire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conformance_error_envelope(tmp_path: Path) -> None:
    db_url = _seed_db(tmp_path / "catalog.db")
    params = _server_params({"TTIO_MCP_DB_URL": db_url})

    async with _session(params) as session:
        r = await _call(session, "ttio_get_file", {"id": 999})

    assert r["ok"] is False
    assert r["error"]["code"] == "not_found"
    assert "999" in r["error"]["message"]
