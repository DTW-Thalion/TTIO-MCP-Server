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
    # OAuth / Keycloak resource-server config (all optional; enabled when oauth_issuer is set)
    oauth_issuer: str | None = None
    oauth_resource_url: str | None = None
    oauth_jwks_url: str | None = None
    oauth_token_url: str | None = None
    oauth_audience: str = "ttio-mcp"
    oauth_exchange_audience: str = "tti-workbench"
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_required_scopes: tuple[str, ...] = ("ttio.connector",)
    oauth_allowed_algs: tuple[str, ...] = ("RS256",)

    @property
    def oauth_enabled(self) -> bool:
        """True when OAuth/Keycloak validation is configured (oauth_issuer is set)."""
        return bool(self.oauth_issuer)

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
            oauth_issuer=os.environ.get("TTIO_MCP_OAUTH_ISSUER") or None,
            oauth_resource_url=os.environ.get("TTIO_MCP_OAUTH_RESOURCE_URL") or None,
            oauth_jwks_url=os.environ.get("TTIO_MCP_OAUTH_JWKS_URL") or None,
            oauth_token_url=os.environ.get("TTIO_MCP_OAUTH_TOKEN_URL") or None,
            oauth_audience=os.environ.get("TTIO_MCP_OAUTH_AUDIENCE", "ttio-mcp"),
            oauth_exchange_audience=os.environ.get("TTIO_MCP_OAUTH_EXCHANGE_AUDIENCE", "tti-workbench"),
            oauth_client_id=os.environ.get("TTIO_MCP_OAUTH_CLIENT_ID") or None,
            oauth_client_secret=os.environ.get("TTIO_MCP_OAUTH_CLIENT_SECRET") or None,
            # Comma-separated; default gates on "ttio.connector". Set the env var
            # (e.g. to "") to override — the realm must actually issue these
            # scopes for the gate to be satisfiable. oauth_allowed_algs keeps its
            # dataclass default (no env override).
            oauth_required_scopes=(
                tuple(
                    s.strip()
                    for s in os.environ["TTIO_MCP_OAUTH_REQUIRED_SCOPES"].split(",")
                    if s.strip()
                )
                if "TTIO_MCP_OAUTH_REQUIRED_SCOPES" in os.environ
                else ("ttio.connector",)
            ),
        )
