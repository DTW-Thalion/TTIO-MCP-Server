# tests/test_tools_sessions.py
import asyncio
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from tests.conftest import FakeWorkbenchClient
from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.tools import sessions as st


@dataclass
class _Sess:
    session_id: str
    status: str


class _Sessions:
    def __init__(self):
        self.terminated = None

    def create(self, *, project, engine_pin, image=None, command=None,
               env=None, bind_mounts=None, container_storage_root=None):
        return _Sess("se1", "starting")

    def list(self, *, status_filter=None, limit=None):
        return [_Sess("se1", "running")]

    def get(self, session_id):
        return _Sess(session_id, "running")

    def terminate(self, session_id):
        self.terminated = session_id


class _Proxy:
    url = "wss://h:18443/v1/sessions/se1/connect"


def _app():
    cm = ConnectionManager()
    fc = FakeWorkbenchClient()
    sess = _Sessions()
    fc.set_subclient("sessions", sess)
    fc.session_proxy = lambda session_id, path="/": _Proxy()
    cm._inject(fc)
    app = FastMCP("t")
    st.register(app, cm, Config.from_env())
    return app, sess


def _call(app, name, **kw):
    res = app._tool_manager.get_tool(name).fn(**kw)
    return asyncio.run(res) if asyncio.iscoroutine(res) else res


def test_session_create():
    app, _ = _app()
    out = _call(app, "ttio_session_create", project="adni", engine_pin="shell")
    assert out["session_id"] == "se1"


def test_session_terminate():
    app, sess = _app()
    out = _call(app, "ttio_session_terminate", session_id="se1")
    assert out["terminated"] == "se1"
    assert sess.terminated == "se1"


def test_session_attach_url():
    app, _ = _app()
    out = _call(app, "ttio_session_attach_url", session_id="se1")
    assert out["attach_url"].endswith("/se1/connect")
