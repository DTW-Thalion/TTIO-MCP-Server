# src/ttio_mcp/tools/cohorts.py
"""Cohort query tools."""
from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP
from ttio.workbench import cohort as C

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import ToolError, to_tool_error

_LEAF_KEYS = {
    "container_field": C.container,
    "subject_field": C.subject,
    "sample_field": C.sample,
}


def predicate_from_json(node: dict[str, Any]) -> C.CohortPredicate:
    """Translate the server's JSON predicate shape into a CohortPredicate tree."""
    op = node.get("op")
    if op == "and":
        children = [predicate_from_json(c) for c in node["children"]]
        out = children[0]
        for c in children[1:]:
            out = out & c
        return out
    if op == "or":
        children = [predicate_from_json(c) for c in node["children"]]
        out = children[0]
        for c in children[1:]:
            out = out | c
        return out
    if op == "not":
        return ~predicate_from_json(node["child"])
    # leaf
    if "phenotype" in node:
        return C.phenotype(node["phenotype"], node.get("op", "eq"), node.get("value"))
    for key, factory in _LEAF_KEYS.items():
        if key in node:
            return factory(node[key], node.get("op", "eq"), node.get("value"))
    raise ToolError(f"Unrecognized predicate node: {sorted(node)}")


def _build_query(select: str, predicate: dict | None, order_by, limit: int, cursor: str | None):
    pred = predicate_from_json(predicate) if predicate else None
    return C.CohortQuery(select=select, predicate=pred,
                         order_by=tuple(order_by or ()), limit=limit, cursor=cursor)


def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:
    @app.tool()
    async def ttio_cohort_query(select: str = "containers", predicate: dict | None = None,
                                order_by: list | None = None, limit: int = 100,
                                cursor: str | None = None) -> dict:
        """Run a cohort query. select=containers|subjects|samples.

        predicate is a JSON tree: leaves use one of container_field / subject_field /
        sample_field / phenotype plus op (eq,ne,lt,gt,le,ge,in,like,exists) and value;
        composites use {"op":"and"|"or","children":[...]} or {"op":"not","child":...}.
        """
        try:
            q = _build_query(select, predicate, order_by, limit, cursor)
            result = await asyncio.to_thread(conn.require_client().query, q)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        rows = [dict(r) for r in result]
        return {"rows": rows, "count": len(rows), "next_cursor": getattr(result, "next_cursor", None)}

    @app.tool()
    async def ttio_cohort_preview_count(select: str = "containers", predicate: dict | None = None) -> dict:
        """Return the row count a cohort query would yield, without fetching rows."""
        try:
            q = _build_query(select, predicate, None, 100, None)
            n = await asyncio.to_thread(conn.require_client().preview_count, q)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"count": int(n), "select": select}
