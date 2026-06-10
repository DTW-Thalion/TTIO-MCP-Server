# src/ttio_mcp/tools/auth.py
"""Authentication / session tools."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager


def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:
    @app.tool()
    def ttio_connection_status() -> dict:
        """Report current workbench connection state (connected, user, projects, capabilities)."""
        return conn.status()
