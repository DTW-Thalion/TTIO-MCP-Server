# src/ttio_mcp/tools/sessions.py
"""Interactive session tools. Attach is exposed as a URL only (no embedded TTY)."""
from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import to_tool_error
from ttio_mcp.tools._serialize import ser as _ser


def _proxy_url(proxy) -> str | None:
    """Extract the WS attach URL from a SessionProxyAttach (or test double).

    The real SDK's SessionProxyAttach stores its parts as private fields and
    exposes no public `url` attribute.  We reconstruct the URL from those
    private fields using the SDK's own `session_proxy_url()` helper.
    Test doubles (which carry a plain `.url` string) are handled first.
    """
    # Test doubles or any future SDK version that exposes a public attribute.
    for attr in ("url", "attach_url"):
        val = getattr(proxy, attr, None)
        if val is not None:
            return val
    # Real SDK: SessionProxyAttach stores _host/_port/_scheme/_session_id.
    host = getattr(proxy, "_host", None)
    port = getattr(proxy, "_port", None)
    session_id = getattr(proxy, "_session_id", None)
    scheme = getattr(proxy, "_scheme", "ws")
    if host and port and session_id:
        try:
            from ttio.workbench.session_proxy import session_proxy_url
            return session_proxy_url(host=host, port=port,
                                     session_id=session_id, scheme=scheme)
        except Exception:
            pass
    return None


def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:
    async def _run(fn, *a, **k):
        return await asyncio.to_thread(fn, *a, **k)

    @app.tool()
    async def ttio_session_create(project: str, engine_pin: str,
                                  image: str | None = None,
                                  command: list | None = None,
                                  env: dict | None = None,
                                  bind_mounts: dict | None = None) -> dict:
        """Start an interactive container session. engine_pin=shell|apptainer|podman|..."""
        try:
            sess = await _run(lambda: conn.require_client().sessions().create(
                project=project, engine_pin=engine_pin, image=image,
                command=command, env=env, bind_mounts=bind_mounts))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return _ser(sess)

    @app.tool()
    async def ttio_sessions_list(status: str | None = None,
                                 limit: int | None = None) -> dict:
        """List sessions in the caller's project scope."""
        try:
            ss = await _run(lambda: conn.require_client().sessions().list(
                status_filter=status, limit=limit))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"sessions": [_ser(s) for s in ss]}

    @app.tool()
    async def ttio_session_get(session_id: str) -> dict:
        """Get a single session row by id."""
        try:
            return _ser(await _run(conn.require_client().sessions().get,
                                   session_id))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}

    @app.tool()
    async def ttio_session_terminate(session_id: str) -> dict:
        """Terminate a session you own."""
        try:
            await _run(conn.require_client().sessions().terminate, session_id)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"terminated": session_id}

    @app.tool()
    async def ttio_session_attach_url(session_id: str, path: str = "/") -> dict:
        """Return the WS attach URL for a running session (connect with your own client).

        The real SDK returns a SessionProxyAttach helper whose URL is built
        from the server endpoint.  No TTY is embedded; the caller uses the
        returned URL to open their own WebSocket connection.
        """
        try:
            proxy = conn.require_client().session_proxy(session_id, path=path)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        url = _proxy_url(proxy)
        return {"attach_url": url, "session_id": session_id}
