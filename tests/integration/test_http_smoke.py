"""Opt-in HTTP transport smoke: start ttio-mcp over streamable-HTTP as a
subprocess and confirm a real MCP client can initialize and list all 28 tools.

Run with: TTIO_MCP_HTTP_SMOKE=1 .venv/bin/python -m pytest tests/integration/test_http_smoke.py
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TTIO_MCP_HTTP_SMOKE") != "1",
    reason="set TTIO_MCP_HTTP_SMOKE=1 to run the HTTP transport smoke",
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_health(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/healthz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("server did not become healthy")


@pytest.mark.asyncio
async def test_http_initialize_and_list_tools():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    port = _free_port()
    env = {
        **os.environ,
        "TTIO_MCP_TRANSPORT": "http",
        "TTIO_MCP_HTTP_PORT": str(port),
        "TTIO_MCP_HTTP_HOST": "127.0.0.1",
    }
    # No TTIO_WB_URL/TOKEN: auto-connect is a no-op; tools still register.
    env.pop("TTIO_WB_URL", None)
    env.pop("TTIO_WB_TOKEN", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "ttio_mcp.server"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_health(port)
        async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                names = {t.name for t in result.tools}
        assert len(names) == 28, sorted(names)
        assert "ttio_login" in names and "ttio_containers_list" in names
    finally:
        proc.terminate()
        proc.wait(timeout=10)
