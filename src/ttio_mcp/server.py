# src/ttio_mcp/server.py
"""FastMCP entry point for ttio-mcp (tti-workbench-server client)."""
from __future__ import annotations

import asyncio
import io
import os

import anyio
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
    from ttio_mcp.tools import sessions as sessions_tools
    sessions_tools.register(app, CONN, CONFIG)
    from ttio_mcp.tools import transfers as transfers_tools
    transfers_tools.register(app, CONN, CONFIG)
    from ttio_mcp.tools import data as data_tools
    data_tools.register(app, CONN, CONFIG)
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


def _reserve_stdout_for_protocol() -> io.TextIOWrapper:
    """Reserve the real stdout (fd 1) exclusively for the MCP JSON-RPC stream.

    An stdio MCP server frames its protocol on stdout, so ANY stray write to
    stdout corrupts it — including C-level writes that Python's
    ``redirect_stdout`` cannot intercept (e.g. liboqs prints a banner to fd 1 on
    import when a PQC transfer runs). We dup the real stdout, then point fd 1 at
    stderr so every other write (``print``, C-level fd-1 writes) lands on stderr.
    The returned stream — over the saved real stdout — is handed to the MCP
    transport so only protocol frames reach the client.
    """
    saved = os.dup(1)
    os.dup2(2, 1)  # fd 1 -> stderr; protects the protocol from stray stdout writes
    return io.TextIOWrapper(os.fdopen(saved, "wb", buffering=0), encoding="utf-8", write_through=True)


async def _serve() -> None:
    from mcp.server.stdio import stdio_server

    protocol_stdout = _reserve_stdout_for_protocol()
    app = build_app()
    srv = app._mcp_server
    async with stdio_server(stdout=anyio.wrap_file(protocol_stdout)) as (read_stream, write_stream):
        await srv.run(read_stream, write_stream, srv.create_initialization_options())


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
