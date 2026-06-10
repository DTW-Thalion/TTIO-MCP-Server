# src/ttio_mcp/tools/sessions.py
"""Interactive session tools. Attach is exposed as a URL only (no embedded TTY)."""
from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import to_tool_error
from ttio_mcp.tools._serialize import ser as _ser


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

        The URL resolves to /v1/sessions/{id}/; `path` is forwarded by the proxy
        inside the engine and does not change the attach URL itself.
        """
        try:
            from ttio.workbench.session_proxy import session_proxy_url
            client = conn.require_client()
            url = session_proxy_url(
                host=client.host,
                port=client.port,
                session_id=session_id,
                scheme=client.ws_scheme,
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"attach_url": url, "session_id": session_id}
