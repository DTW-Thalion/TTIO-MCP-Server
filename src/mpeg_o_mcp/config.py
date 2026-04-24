"""Environment-driven configuration for the MPEG-O MCP server.

M4 adds ``fsspec_kwargs`` — a default JSON dict forwarded to
``fsspec.open`` for cloud URIs (``s3://``, ``https://``, ...).
Per-call kwargs on ``mpgo_register_file`` / ``mpgo_get_spectrum``
shallow-merge on top of this default, with per-call keys winning.

Keyring, encryption, and per-user auth land in M5.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

DEFAULT_DB_URL = "sqlite:///mpeg_o_mcp.db"


@dataclass(frozen=True)
class Config:
    db_url: str
    fsspec_kwargs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> Config:
        raw = os.environ.get("MPGO_MCP_FSSPEC_KWARGS", "").strip()
        kwargs: dict[str, Any] = {}
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"MPGO_MCP_FSSPEC_KWARGS is not valid JSON: {exc}"
                ) from exc
            if not isinstance(parsed, dict):
                raise ValueError(
                    "MPGO_MCP_FSSPEC_KWARGS must be a JSON object"
                )
            kwargs = parsed
        return cls(
            db_url=os.environ.get("MPGO_MCP_DB_URL", DEFAULT_DB_URL),
            fsspec_kwargs=kwargs,
        )
