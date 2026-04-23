"""Environment-driven configuration for the MPEG-O MCP server.

Secrets (keyring paths, cloud credentials, fsspec config) land in M4.
This module intentionally only exposes what M1 needs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_DB_URL = "sqlite:///mpeg_o_mcp.db"


@dataclass(frozen=True)
class Config:
    db_url: str

    @classmethod
    def from_env(cls) -> Config:
        return cls(db_url=os.environ.get("MPGO_MCP_DB_URL", DEFAULT_DB_URL))
