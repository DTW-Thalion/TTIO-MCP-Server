"""Tests for the ``ttio_launch_uploader`` MCP tool.

The tool shells out to ``python -m ttio_mcp.uploader`` — a tkinter
subprocess that can't run headless in CI. These tests mock
``subprocess.run`` so the handler's plumbing (env check, JSON parsing,
error translation) is exercised without needing a display.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from ttio_mcp.catalog import CatalogError
from ttio_mcp.tools.launch_uploader import handle


def _fake_completed(
    stdout: str, *, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["python", "-m", "ttio_mcp.uploader"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


async def test_intake_not_configured(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TTIO_MCP_INTAKE_DIR", raising=False)
    with pytest.raises(CatalogError) as exc_info:
        await handle(session, {})
    assert exc_info.value.code == "intake_not_configured"


async def test_happy_path_returns_uploader_payload(
    session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    intake = tmp_path / "intake"
    intake.mkdir()
    monkeypatch.setenv("TTIO_MCP_INTAKE_DIR", str(intake))

    payload = {
        "ok": True,
        "data": {
            "source": str(tmp_path / "source.mpgo"),
            "destination": str(intake / "source.mpgo"),
            "format": "mpgo",
            "size_bytes": 42,
        },
    }
    with patch(
        "ttio_mcp.tools.launch_uploader.subprocess.run",
        return_value=_fake_completed(json.dumps(payload) + "\n"),
    ):
        result = await handle(session, {})

    assert result["format"] == "mpgo"
    assert result["size_bytes"] == 42
    assert result["destination"] == str(intake / "source.mpgo")
    assert Path(result["intake_dir"]) == intake.resolve()


async def test_uploader_error_becomes_catalog_error(
    session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TTIO_MCP_INTAKE_DIR", str(tmp_path))
    payload = {
        "ok": False,
        "error": {"code": "cancelled", "message": "User cancelled the file picker."},
    }
    with patch(
        "ttio_mcp.tools.launch_uploader.subprocess.run",
        return_value=_fake_completed(json.dumps(payload), returncode=2),
    ):
        with pytest.raises(CatalogError) as exc_info:
            await handle(session, {})
    assert exc_info.value.code == "cancelled"
    assert "cancelled" in str(exc_info.value).lower()


async def test_timeout_becomes_catalog_error(
    session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TTIO_MCP_INTAKE_DIR", str(tmp_path))

    def _raise(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 1))

    with patch(
        "ttio_mcp.tools.launch_uploader.subprocess.run",
        side_effect=_raise,
    ):
        with pytest.raises(CatalogError) as exc_info:
            await handle(session, {"timeout_seconds": 1})
    assert exc_info.value.code == "timeout"


async def test_invalid_json_from_uploader(
    session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TTIO_MCP_INTAKE_DIR", str(tmp_path))
    with patch(
        "ttio_mcp.tools.launch_uploader.subprocess.run",
        return_value=_fake_completed("not json at all"),
    ):
        with pytest.raises(CatalogError) as exc_info:
            await handle(session, {})
    assert exc_info.value.code == "upload_failed"


async def test_empty_stdout(
    session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TTIO_MCP_INTAKE_DIR", str(tmp_path))
    with patch(
        "ttio_mcp.tools.launch_uploader.subprocess.run",
        return_value=_fake_completed("", returncode=3, stderr="boom"),
    ):
        with pytest.raises(CatalogError) as exc_info:
            await handle(session, {})
    assert exc_info.value.code == "upload_failed"
