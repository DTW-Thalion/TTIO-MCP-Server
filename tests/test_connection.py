# tests/test_connection.py
import pytest

from tests.conftest import FakeSession, FakeWorkbenchClient
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import ToolError


def test_require_client_when_disconnected_raises():
    cm = ConnectionManager()
    with pytest.raises(ToolError) as ei:
        cm.require_client()
    assert "not connected" in str(ei.value).lower()


def test_inject_and_require():
    cm = ConnectionManager()
    fc = FakeWorkbenchClient()
    cm._inject(fc)
    assert cm.require_client() is fc


def test_expired_session_raises():
    cm = ConnectionManager()
    cm._inject(FakeWorkbenchClient(session=FakeSession(expired=True)))
    with pytest.raises(ToolError) as ei:
        cm.require_client()
    assert "expired" in str(ei.value).lower()


def test_bearer_zero_expiry_not_rejected():
    # BearerAuth / API-key sessions synthesize expires_at == 0 (never expires);
    # the SDK's .expired reads epoch 0 as long-past, but we must NOT reject it.
    sess = FakeSession(expired=True)
    sess.expires_at = 0
    cm = ConnectionManager()
    fc = FakeWorkbenchClient(session=sess)
    cm._inject(fc)
    assert cm.require_client() is fc
    assert cm.status()["expired"] is False


def test_status_disconnected():
    cm = ConnectionManager()
    st = cm.status()
    assert st["connected"] is False


def test_status_connected():
    cm = ConnectionManager()
    cm._inject(FakeWorkbenchClient())
    st = cm.status()
    assert st["connected"] is True
    assert st["username"] == "alice"


class _Client:
    def __init__(self, name):
        self.session = type(
            "S", (),
            {"username": name, "projects": ("p",), "capabilities": (),
             "expires_at": 9999999999, "expired": False},
        )()


def test_sessions_are_isolated():
    cm = ConnectionManager()
    key = {"v": "A"}
    cm.bind_session_resolver(lambda: key["v"])
    cm._inject(_Client("alice"))                 # stored under key A
    assert cm.require_client().session.username == "alice"
    key["v"] = "B"                                # switch session
    with pytest.raises(ToolError):
        cm.require_client()                       # B has no client
    key["v"] = "A"
    assert cm.require_client().session.username == "alice"  # A unaffected


def test_logout_scoped_to_current_session():
    cm = ConnectionManager()
    key = {"v": "A"}
    cm.bind_session_resolver(lambda: key["v"])
    cm._inject(_Client("a"))
    key["v"] = "B"
    cm._inject(_Client("b"))
    cm.logout()                                   # drops B only
    with pytest.raises(ToolError):
        cm.require_client()
    key["v"] = "A"
    assert cm.require_client().session.username == "a"


def test_resolver_failure_falls_back_to_default():
    cm = ConnectionManager()

    def boom():
        raise RuntimeError("no request")

    cm.bind_session_resolver(boom)
    cm._inject(_Client("d"))                      # falls back to default key
    assert cm.require_client().session.username == "d"


def test_registry_evicts_lru_over_cap():
    cm = ConnectionManager(max_sessions=2)
    key = {"v": 0}
    cm.bind_session_resolver(lambda: key["v"])
    for i in range(3):
        key["v"] = i
        cm._inject(_Client(f"c{i}"))
    key["v"] = 0
    with pytest.raises(ToolError):                # key 0 evicted (LRU)
        cm.require_client()
    key["v"] = 2
    assert cm.require_client().session.username == "c2"
