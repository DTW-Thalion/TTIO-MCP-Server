# tests/test_tools_containers.py
import asyncio
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from tests.conftest import FakeWorkbenchClient
from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.tools import containers as ct


@dataclass
class _Page:
    containers: list
    next_cursor: str | None
    has_more: bool


@dataclass
class _C:
    uri: str
    project: str
    owner: str
    encrypted: bool


class _Containers:
    def list(self, project=None, owner=None, limit=None, cursor=None):
        return _Page(containers=[_C("uri:tio:1", "adni", "alice", False)],
                     next_cursor=None, has_more=False)

    def get(self, uri):
        return _C(uri, "adni", "alice", False)

    def layers(self, uri):
        return []

    def manifest(self, uri):
        return _C(uri, "adni", "alice", False)


def _app():
    cm = ConnectionManager()
    fc = FakeWorkbenchClient()
    fc.set_subclient("containers", _Containers())
    cm._inject(fc)
    app = FastMCP("t")
    ct.register(app, cm, Config.from_env())
    return app


def _call(app, name, **kw):
    tool = app._tool_manager.get_tool(name)
    res = tool.fn(**kw)
    return asyncio.run(res) if asyncio.iscoroutine(res) else res


def test_containers_list():
    out = _call(_app(), "ttio_containers_list")
    assert out["containers"][0]["uri"] == "uri:tio:1"
    assert out["has_more"] is False


def test_container_get():
    out = _call(_app(), "ttio_container_get", uri="uri:tio:1")
    assert out["uri"] == "uri:tio:1"
