# src/ttio_mcp/tools/auth.py
"""Authentication / session tools."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import to_tool_error


def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:
    @app.tool()
    def ttio_login(username: str, password: str, totp: str, url: str | None = None) -> dict:
        """Log in to the workbench server with username + password + current 6-digit TOTP.

        ``url`` defaults to the configured TTIO_WB_URL. Starts an in-memory
        session (expires ~24h). For headless use, set TTIO_WB_URL + TTIO_WB_TOKEN
        (an API key) instead and the server auto-connects at startup.
        """
        target = url or config.url
        if not target:
            return {"connected": False, "error": "No server URL: pass url= or set TTIO_WB_URL."}
        try:
            return conn.login_password(target, username, password, totp)
        except Exception as exc:  # noqa: BLE001 - surfaced as a tool message
            return {"connected": False, "error": to_tool_error(exc)}

    @app.tool()
    def ttio_whoami() -> dict:
        """Return the current session identity (username, projects, capabilities)."""
        return conn.status()

    @app.tool()
    def ttio_logout() -> dict:
        """Drop the in-memory session (client-side only; tokens are not persisted)."""
        conn.logout()
        return conn.status()

    @app.tool()
    def ttio_connection_status() -> dict:
        """Report current workbench connection state."""
        return conn.status()
