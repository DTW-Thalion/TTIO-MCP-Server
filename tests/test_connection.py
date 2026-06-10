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
