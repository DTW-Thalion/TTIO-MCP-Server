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


# ---------------------------------------------------------------------------
# Task 5: per-session OAuth token-exchange connect
# ---------------------------------------------------------------------------
import ttio_mcp.connection as connection  # noqa: E402 – module needed for monkeypatching


def test_oauth_session_connect(monkeypatch):
    """OAuth enabled + valid access token → exchange fires and client is built."""
    cm = ConnectionManager()

    # OAuth config with the minimal fields _maybe_oauth_connect accesses.
    class _Cfg:
        url = "wss://wb/transport"
        oauth_token_url = "https://kc/token"
        oauth_client_id = "ttio-mcp"
        oauth_client_secret = "s3cr3t"
        oauth_exchange_audience = "tti-workbench"

    cm.enable_oauth(_Cfg())

    # Simulate a validated AccessToken already in the request contextvar.
    class _AT:
        token = "user.jwt"
        subject = "01HACCT"
        claims = {"preferred_username": "alice"}
        expires_at = 0

    monkeypatch.setattr(connection, "_current_access_token", lambda: _AT())

    # Stub the async exchange to avoid real HTTP; count invocations.
    calls = []

    async def _fake_exchange(**kw):
        calls.append(kw)
        return ("wb.jwt", 0)

    monkeypatch.setattr(connection, "exchange_for_workbench", _fake_exchange)

    # Capture the BearerAuth passed to ttio.connect.
    built: dict = {}

    def _fake_connect(url, auth=None):
        built["url"] = url
        built["token"] = auth.token
        built["user"] = auth.username_

        class _C:
            session = None

        return _C()

    monkeypatch.setattr(connection.ttio, "connect", _fake_connect)

    client = cm.require_client()
    assert client is not None
    assert built["token"] == "wb.jwt"
    assert built["user"] == "alice"
    assert len(calls) == 1
    # A second call in the same session reuses the cached client (no re-exchange).
    assert cm.require_client() is client
    assert len(calls) == 1


def test_oauth_no_access_token_raises(monkeypatch):
    """OAuth enabled but no access token in context → ToolError (not connected)."""
    cm = ConnectionManager()

    class _Cfg:
        url = "wss://wb/transport"
        oauth_token_url = "https://kc/token"
        oauth_client_id = "ttio-mcp"
        oauth_client_secret = "s"
        oauth_exchange_audience = "tti-workbench"

    cm.enable_oauth(_Cfg())
    monkeypatch.setattr(connection, "_current_access_token", lambda: None)

    with pytest.raises(ToolError):
        cm.require_client()


def test_oauth_exchange_failure_raises_tool_error(monkeypatch):
    """A failed token exchange surfaces as a clean ToolError, not a raw traceback."""
    cm = ConnectionManager()

    class _Cfg:
        url = "wss://wb/transport"
        oauth_token_url = "https://kc/token"
        oauth_client_id = "ttio-mcp"
        oauth_client_secret = "s3cr3t"
        oauth_exchange_audience = "tti-workbench"

    cm.enable_oauth(_Cfg())

    class _AT:
        token = "user.jwt"
        subject = "01HACCT"
        claims = {"preferred_username": "alice"}
        expires_at = 0

    monkeypatch.setattr(connection, "_current_access_token", lambda: _AT())

    async def _boom(**kw):
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(connection, "exchange_for_workbench", _boom)

    with pytest.raises(ToolError, match="token exchange failed"):
        cm.require_client()
