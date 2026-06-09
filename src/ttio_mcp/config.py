"""Environment-driven configuration for the TTI-O MCP server.

M4 adds ``fsspec_kwargs`` — a default JSON dict forwarded to
``fsspec.open`` for cloud URIs (``s3://``, ``https://``, ...).
Per-call kwargs on ``ttio_register_file`` / ``ttio_get_spectrum``
shallow-merge on top of this default, with per-call keys winning.

M5 adds ``keyring_path`` (``TTIO_KEYRING_PATH``) — a JSON file that
maps ``key_id`` to base64-encoded AES-256-GCM bytes. See
:mod:`ttio_mcp.keyring` for the file format.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_DB_URL = "sqlite:///ttio_mcp.db"


@dataclass(frozen=True)
class Config:
    db_url: str
    fsspec_kwargs: dict[str, Any] = field(default_factory=dict)
    keyring_path: Path | None = None
    intake_dir: Path | None = None

    @classmethod
    def from_env(cls) -> Config:
        raw = os.environ.get("TTIO_MCP_FSSPEC_KWARGS", "").strip()
        kwargs: dict[str, Any] = {}
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"TTIO_MCP_FSSPEC_KWARGS is not valid JSON: {exc}"
                ) from exc
            if not isinstance(parsed, dict):
                raise ValueError(
                    "TTIO_MCP_FSSPEC_KWARGS must be a JSON object"
                )
            kwargs = parsed

        keyring_raw = os.environ.get("TTIO_KEYRING_PATH", "").strip()
        keyring_path = Path(keyring_raw).expanduser() if keyring_raw else None

        intake_raw = os.environ.get("TTIO_MCP_INTAKE_DIR", "").strip()
        intake_dir = Path(intake_raw).expanduser().resolve() if intake_raw else None

        return cls(
            db_url=os.environ.get("TTIO_MCP_DB_URL", DEFAULT_DB_URL),
            fsspec_kwargs=kwargs,
            keyring_path=keyring_path,
            intake_dir=intake_dir,
        )
