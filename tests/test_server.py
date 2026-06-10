# tests/test_server.py
import asyncio

from ttio_mcp.server import CONN, build_app


def test_build_app_returns_fastmcp_and_registers_tools():
    app = build_app()
    names = asyncio.run(_tool_names(app))
    # auth tools must be registered by the skeleton wiring
    assert "ttio_connection_status" in names


async def _tool_names(app):
    tools = await app.list_tools()
    return {t.name for t in tools}


def test_conn_singleton_exists():
    assert CONN is not None
    assert CONN.status()["connected"] in (True, False)
