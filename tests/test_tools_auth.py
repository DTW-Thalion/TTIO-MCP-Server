# tests/test_tools_auth.py
from mcp.server.fastmcp import FastMCP

from tests.conftest import FakeWorkbenchClient
from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.tools import auth as auth_tools


def _fn(app, name):
    # FastMCP stores the python callable on the registered Tool object.
    tool = app._tool_manager.get_tool(name)
    return tool.fn


def test_login_password_delegates(monkeypatch):
    cm = ConnectionManager()
    captured = {}

    def fake_login(url, username, password, totp):
        captured.update(url=url, username=username, password=password, totp=totp)
        cm._inject(FakeWorkbenchClient())
        return cm.status()

    monkeypatch.setattr(cm, "login_password", fake_login)
    app = FastMCP("t")
    auth_tools.register(app, cm, Config.from_env())
    out = _fn(app, "ttio_login")(url="wss://h:18443/transport",
                                 username="alice", password="pw", totp="123456")
    assert out["connected"] is True
    assert captured["username"] == "alice"


def test_whoami_requires_connection():
    cm = ConnectionManager()
    app = FastMCP("t")
    auth_tools.register(app, cm, Config.from_env())
    out = _fn(app, "ttio_whoami")()
    assert out["connected"] is False


def test_logout_clears():
    cm = ConnectionManager()
    cm._inject(FakeWorkbenchClient())
    app = FastMCP("t")
    auth_tools.register(app, cm, Config.from_env())
    out = _fn(app, "ttio_logout")()
    assert out["connected"] is False
