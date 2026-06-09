"""``mpgo_launch_uploader`` — spawn the local GUI uploader.

The server and MCP client run on the same machine (stdio transport),
so the server can launch a tkinter file-picker on the user's desktop.
The picker copies the chosen file into ``MPGO_MCP_INTAKE_DIR`` and
emits a single JSON line to stdout, which this handler parses and
returns to the caller.

No catalog rows are written here — a subsequent ``mpgo_register_file``
call (pointing at the returned ``destination``) is still required to
bring the file into the catalog.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from sqlalchemy.orm import Session

from mpeg_o_mcp.catalog import CatalogError
from mpeg_o_mcp.uploader.core import get_intake_dir

DEFAULT_TIMEOUT_SECONDS = 600

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "timeout_seconds": {
            "type": "integer",
            "minimum": 1,
            "maximum": 3600,
            "description": (
                "How long to wait for the user to pick a file, in seconds. "
                f"Default {DEFAULT_TIMEOUT_SECONDS}."
            ),
        },
    },
}


async def handle(session: Session, args: dict[str, Any]) -> dict[str, Any]:
    timeout = int(args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))

    intake_dir = get_intake_dir()
    if intake_dir is None:
        raise CatalogError(
            "MPGO_MCP_INTAKE_DIR is not set — the server has no configured "
            "destination for uploaded files.",
            code="intake_not_configured",
        )

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "mpeg_o_mcp.uploader"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CatalogError(
            f"file picker did not complete within {timeout}s",
            code="timeout",
        ) from exc

    stdout = (proc.stdout or "").strip()
    if not stdout:
        raise CatalogError(
            "uploader produced no output "
            f"(exit code {proc.returncode}, stderr: {proc.stderr!r})",
            code="upload_failed",
        )

    try:
        payload = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError as exc:
        raise CatalogError(
            f"uploader returned invalid JSON: {stdout!r}",
            code="upload_failed",
        ) from exc

    if not isinstance(payload, dict):
        raise CatalogError(
            f"uploader returned non-object JSON: {payload!r}",
            code="upload_failed",
        )

    if not payload.get("ok"):
        err = payload.get("error") or {}
        raise CatalogError(
            str(err.get("message") or "uploader reported failure"),
            code=str(err.get("code") or "upload_failed"),
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        raise CatalogError(
            f"uploader success payload missing data object: {payload!r}",
            code="upload_failed",
        )

    return {
        "intake_dir": str(intake_dir),
        **data,
    }
