# src/ttio_mcp/server.py
"""FastMCP entry point for ttio-mcp (tti-workbench-server client)."""
from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager

# Process-wide singletons.
CONN = ConnectionManager()
CONFIG = Config.from_env()


def build_app() -> FastMCP:
    app = FastMCP("ttio-mcp")
    from ttio_mcp.tools import auth as auth_tools

    auth_tools.register(app, CONN, CONFIG)
    from ttio_mcp.tools import containers as containers_tools
    containers_tools.register(app, CONN, CONFIG)
    from ttio_mcp.tools import cohorts as cohorts_tools
    cohorts_tools.register(app, CONN, CONFIG)
    from ttio_mcp.tools import jobs as jobs_tools
    jobs_tools.register(app, CONN, CONFIG)
    # Later phases append: sessions, transfers, data.
    _maybe_autoconnect()
    return app


def _maybe_autoconnect() -> None:
    """If a URL + token are configured, pre-connect with a bearer/API key."""
    if CONFIG.url and CONFIG.token:
        try:
            CONN.login_token(CONFIG.url, CONFIG.token, CONFIG.username)
        except Exception:
            # Leave disconnected; ttio_connection_status will report it.
            pass


def main() -> None:
    app = build_app()
    asyncio.run(app.run_stdio_async())


if __name__ == "__main__":
    main()
