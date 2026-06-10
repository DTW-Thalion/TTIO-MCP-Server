# src/ttio_mcp/tools/jobs.py
"""Jobs + pipelines tools (no pipeline registration — that is admin)."""
from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from mcp.server.fastmcp import FastMCP
from ttio.workbench.jobs import build_cohort_input

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
    async def ttio_job_submit(pipeline_id: str, inputs: dict, params: dict | None = None) -> dict:
        """Submit a pipeline job. inputs maps slot->container_uri; a slot value of
        {"cohort_query": <query-json>} is auto-wrapped as a cohort input."""
        try:
            norm = {k: (build_cohort_input(v["cohort_query"]) if isinstance(v, dict) and "cohort_query" in v else v)
                    for k, v in inputs.items()}
            job = await _run(lambda: conn.require_client().jobs().submit(
                pipeline_id=pipeline_id, inputs=norm, params=params))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return _ser(job)

    @app.tool()
    async def ttio_jobs_list(status: str | None = None, limit: int | None = None) -> dict:
        """List jobs in the caller's project scope (optional status filter)."""
        try:
            jobs = await _run(lambda: conn.require_client().jobs().list(status_filter=status, limit=limit))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"jobs": [_ser(j) for j in jobs]}

    @app.tool()
    async def ttio_job_get(job_id: str) -> dict:
        """Get a single job row by id."""
        try:
            return _ser(await _run(conn.require_client().jobs().get, job_id))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}

    @app.tool()
    async def ttio_job_cancel(job_id: str) -> dict:
        """Cancel a job you own."""
        try:
            await _run(conn.require_client().jobs().cancel, job_id)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"cancelled": job_id}

    @app.tool()
    async def ttio_job_events(job_id: str, max_events: int = 20) -> dict:
        """Tail a job's live event stream (SSE); returns up to max_events then stops."""
        events: list[Any] = []
        try:
            client = conn.require_client()
            async for evt in client.jobs().events(job_id):
                events.append(_ser(evt))
                if len(events) >= max_events:
                    break
        except Exception as exc:  # noqa: BLE001
            return {"events": [_ser(e) for e in events], "error": to_tool_error(exc)}
        return {"events": events}

    @app.tool()
    async def ttio_pipelines_list() -> dict:
        """List pipelines visible to the caller's project scope."""
        try:
            ps = await _run(conn.require_client().pipelines().list)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"pipelines": [_ser(p) for p in ps]}

    @app.tool()
    async def ttio_pipeline_get(pipeline_id: str) -> dict:
        """Get a single pipeline definition by id."""
        try:
            return _ser(await _run(conn.require_client().pipelines().get, pipeline_id))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
