# tests/test_tools_cohorts.py
import asyncio
from dataclasses import dataclass

import pytest
from mcp.server.fastmcp import FastMCP

from tests.conftest import FakeWorkbenchClient
from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import ToolError
from ttio_mcp.tools import cohorts as co
from ttio_mcp.tools.cohorts import predicate_from_json


def test_predicate_from_json_leaf():
    p = predicate_from_json({"container_field": "owner", "op": "eq", "value": "alice"})
    assert p.to_json() == {"container_field": "owner", "op": "eq", "value": "alice"}


def test_predicate_from_json_composite():
    tree = {"op": "and", "children": [
        {"container_field": "owner", "op": "eq", "value": "alice"},
        {"subject_field": "sex", "op": "eq", "value": "F"},
    ]}
    p = predicate_from_json(tree)
    js = p.to_json()
    assert js["op"] == "and"
    assert len(js["children"]) == 2


def test_predicate_from_json_not():
    p = predicate_from_json({"op": "not", "child": {"container_field": "encrypted", "op": "eq", "value": True}})
    assert p.to_json()["op"] == "not"


@dataclass
class _Result:
    rows: list
    next_cursor: str | None
    def __iter__(self): return iter(self.rows)
    def __len__(self): return len(self.rows)


def _app(result=None, count=0):
    cm = ConnectionManager()
    fc = FakeWorkbenchClient()
    fc.set_subclient("query_result", result or _Result([{"uri": "uri:tio:1"}], None))
    fc.set_subclient("preview_count", count)
    cm._inject(fc)
    app = FastMCP("t")
    co.register(app, cm, Config.from_env())
    return app, fc


def _call(app, name, **kw):
    res = app._tool_manager.get_tool(name).fn(**kw)
    return asyncio.run(res) if asyncio.iscoroutine(res) else res


def test_cohort_query():
    app, fc = _app()
    out = _call(app, "ttio_cohort_query", select="containers",
                predicate={"container_field": "owner", "op": "eq", "value": "alice"})
    assert out["rows"][0]["uri"] == "uri:tio:1"
    assert fc.calls[0][0] == "query"


def test_cohort_preview_count():
    app, fc = _app(count=42)
    out = _call(app, "ttio_cohort_preview_count", select="subjects")
    assert out["count"] == 42


def test_predicate_empty_children_raises():
    with pytest.raises(ToolError):
        predicate_from_json({"op": "and", "children": []})


def test_predicate_missing_children_raises():
    with pytest.raises(ToolError):
        predicate_from_json({"op": "or"})


def test_predicate_not_missing_child_raises():
    with pytest.raises(ToolError):
        predicate_from_json({"op": "not"})
