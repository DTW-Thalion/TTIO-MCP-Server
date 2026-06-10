# src/ttio_mcp/tools/containers.py
"""Container browsing tools (read-only; no delete)."""
from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import to_tool_error


def _ser(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _ser(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_ser(x) for x in obj]
    return obj


def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:
    async def _run(fn, *a, **k):
        return await asyncio.to_thread(fn, *a, **k)

    @app.tool()
    async def ttio_containers_list(project: str | None = None, owner: str | None = None,
                                   limit: int | None = None, cursor: str | None = None) -> dict:
        """List server containers (paginated). Filters: project, owner. Use cursor to page."""
        try:
            cc = conn.require_client().containers()
            page = await _run(cc.list, project, owner, limit or config.page_size, cursor)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {
            "containers": [_ser(c) for c in page.containers],
            "next_cursor": getattr(page, "next_cursor", None),
            "has_more": bool(getattr(page, "has_more", False)),
        }

    @app.tool()
    async def ttio_container_get(uri: str) -> dict:
        """Get one container's detail row + file stats by URI."""
        try:
            return _ser(await _run(conn.require_client().containers().get, uri))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}

    @app.tool()
    async def ttio_container_layers(uri: str) -> dict:
        """List a container's auxiliary layers."""
        try:
            layers = await _run(conn.require_client().containers().layers, uri)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"layers": [_ser(x) for x in layers]}

    @app.tool()
    async def ttio_container_manifest(uri: str) -> dict:
        """Get a container's HDF5 manifest projection (runs, counts, ISA ids)."""
        try:
            return _ser(await _run(conn.require_client().containers().manifest, uri))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
