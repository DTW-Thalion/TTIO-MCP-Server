# src/ttio_mcp/config.py
"""Runtime configuration for the workbench MCP server."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "ttio-mcp"


@dataclass(frozen=True)
class Config:
    """Server configuration, populated from environment variables.

    No secrets are persisted; ``token`` (an API key or bearer) is read
    from the environment only.
    """

    url: str | None
    token: str | None
    username: str | None
    export_dir: Path
    cache_dir: Path
    page_size: int
    transport: str
    http_host: str
    http_port: int
    http_path: str

    @classmethod
    def from_env(cls) -> Config:
        state = _default_state_dir()
        export_dir = Path(os.environ.get("TTIO_MCP_EXPORT_DIR", state / "exports"))
        cache_dir = Path(os.environ.get("TTIO_MCP_CACHE_DIR", state / "cache"))
        page_size = int(os.environ.get("TTIO_MCP_PAGE_SIZE", "100"))
        transport = os.environ.get("TTIO_MCP_TRANSPORT", "stdio")
        if transport not in ("stdio", "http"):
            raise ValueError(
                f"TTIO_MCP_TRANSPORT must be 'stdio' or 'http', got {transport!r}"
            )
        http_host = os.environ.get("TTIO_MCP_HTTP_HOST", "127.0.0.1")
        http_port = int(os.environ.get("TTIO_MCP_HTTP_PORT", "8000"))
        http_path = os.environ.get("TTIO_MCP_HTTP_PATH", "/mcp")
        return cls(
            url=os.environ.get("TTIO_WB_URL") or None,
            token=os.environ.get("TTIO_WB_TOKEN") or None,
            username=os.environ.get("TTIO_WB_USERNAME") or None,
            export_dir=export_dir,
            cache_dir=cache_dir,
            page_size=page_size,
            transport=transport,
            http_host=http_host,
            http_port=http_port,
            http_path=http_path,
        )
