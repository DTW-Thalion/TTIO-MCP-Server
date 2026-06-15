"""Opt-in HTTP OAuth smoke: start ttio-mcp in OAuth resource-server mode and
verify the protected-resource metadata route, the unauthenticated 401, and
(when a user token is provided) an authenticated MCP session.

Run with:
    TTIO_MCP_OAUTH_SMOKE=1 \\
    TTIO_MCP_OAUTH_ISSUER="https://kc.example.com/realms/ttio" \\
    TTIO_MCP_OAUTH_RESOURCE_URL="http://127.0.0.1:<port>/mcp" \\
    TTIO_MCP_OAUTH_JWKS_URL="https://kc.example.com/realms/ttio/protocol/openid-connect/certs" \\
    TTIO_MCP_OAUTH_TOKEN_URL="https://kc.example.com/realms/ttio/protocol/openid-connect/token" \\
    TTIO_MCP_OAUTH_CLIENT_ID="ttio-mcp" \\
    TTIO_MCP_OAUTH_CLIENT_SECRET="..." \\
    [TTIO_MCP_OAUTH_USER_TOKEN="<user-access-token>"] \\
    .venv/bin/python -m pytest tests/integration/test_http_oauth_smoke.py -v

Without TTIO_MCP_OAUTH_USER_TOKEN only the metadata and 401 assertions run
(the server does not need Keycloak to be reachable for those two checks).
With the token the test also establishes a real MCP session and lists tools.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TTIO_MCP_OAUTH_SMOKE") != "1",
    reason="set TTIO_MCP_OAUTH_SMOKE=1 to run the OAuth HTTP transport smoke",
)


# ---------------------------------------------------------------------------
# Helpers (mirror test_http_smoke.py)
# ---------------------------------------------------------------------------

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
    raise RuntimeError("server did not become healthy within timeout")


def _oauth_env(port: int) -> dict[str, str]:
    """Build the subprocess environment for OAuth mode.

    Reads the OAuth variables from the caller's environment.  At minimum
    TTIO_MCP_OAUTH_ISSUER must be set (it enables OAuth mode in the server).
    The RESOURCE_URL is overridden to point at the ephemeral port so the
    metadata body is consistent regardless of what the caller passed in.
    """
    env = {**os.environ}
    # Required for the server to start in the right mode
    env["TTIO_MCP_TRANSPORT"] = "http"
    env["TTIO_MCP_HTTP_PORT"] = str(port)
    env["TTIO_MCP_HTTP_HOST"] = "127.0.0.1"
    # Override the resource URL to match the actual ephemeral port
    env["TTIO_MCP_OAUTH_RESOURCE_URL"] = f"http://127.0.0.1:{port}/mcp"
    # Clear headless workbench tokens so we don't accidentally auto-connect
    env.pop("TTIO_WB_TOKEN", None)
    return env


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_oauth_protected_resource_metadata():
    """GET /.well-known/oauth-protected-resource/mcp returns 200 with resource
    and authorization_servers fields."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "ttio_mcp.server"],
        env=_oauth_env(port),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_health(port)
        import json
        url = f"http://127.0.0.1:{port}/.well-known/oauth-protected-resource/mcp"
        with urllib.request.urlopen(url, timeout=5) as r:
            assert r.status == 200, f"expected 200, got {r.status}"
            body = json.loads(r.read())
        assert "resource" in body, f"missing 'resource' key: {body}"
        assert "authorization_servers" in body, f"missing 'authorization_servers' key: {body}"
        issuer = os.environ.get("TTIO_MCP_OAUTH_ISSUER", "")
        if issuer:
            assert issuer in body["authorization_servers"], (
                f"issuer {issuer!r} not in authorization_servers: {body['authorization_servers']}"
            )
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.asyncio
async def test_oauth_unauthenticated_post_returns_401():
    """POST /mcp with no Authorization header returns 401 and a
    WWW-Authenticate: Bearer header."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "ttio_mcp.server"],
        env=_oauth_env(port),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_health(port)
        import http.client
        import json as _json

        conn = http.client.HTTPConnection("127.0.0.1", port)
        payload = _json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "smoke", "version": "0"}}}
        ).encode()
        conn.request(
            "POST", "/mcp",
            body=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 401, f"expected 401, got {resp.status}"
        www_auth = resp.getheader("www-authenticate", "")
        assert "Bearer" in www_auth, (
            f"expected 'Bearer' in WWW-Authenticate, got {www_auth!r}"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.asyncio
async def test_oauth_authenticated_session_lists_tools():
    """When TTIO_MCP_OAUTH_USER_TOKEN is set, POST /mcp with that bearer token
    successfully initialises an MCP session and lists all 28 tools.

    Skipped when the env var is absent (Keycloak not available).
    """
    user_token = os.environ.get("TTIO_MCP_OAUTH_USER_TOKEN")
    if not user_token:
        pytest.skip("TTIO_MCP_OAUTH_USER_TOKEN not set; skipping authenticated portion")

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    port = _free_port()
    env = _oauth_env(port)
    proc = subprocess.Popen(
        [sys.executable, "-m", "ttio_mcp.server"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_health(port)
        mcp_url = f"http://127.0.0.1:{port}/mcp"
        headers = {"Authorization": f"Bearer {user_token}"}
        async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                names = {t.name for t in result.tools}
        assert len(names) == 28, sorted(names)
        assert "ttio_login" in names and "ttio_containers_list" in names
    finally:
        proc.terminate()
        proc.wait(timeout=10)
