# tests/conftest.py
"""Shared test doubles for the workbench MCP server."""
from __future__ import annotations

import pytest

from ttio_mcp.connection import ConnectionManager


class FakeSession:
    def __init__(self, *, expired=False, username="alice", token="ttiowbs_x",
                 capabilities=(), projects=("adni",)):
        self.expired = expired
        self.username = username
        self.token = token
        self.user_id = "u1"
        self.capabilities = frozenset(capabilities)
        self.projects = tuple(projects)
        self.expires_at = 0 if expired else 9999999999
        self.provider = "password-totp"
        self.session_id = "s1"


class FakeWorkbenchClient:
    """Records calls and returns canned objects. Sub-client factories
    return objects supplied by the test via ``.set_subclient``."""

    def __init__(self, session=None):
        self.session = session or FakeSession()
        self._subclients: dict[str, object] = {}
        self.calls: list[tuple] = []

    def set_subclient(self, name, obj):
        self._subclients[name] = obj

    def containers(self):
        return self._subclients["containers"]

    def jobs(self):
        return self._subclients["jobs"]

    def pipelines(self):
        return self._subclients["pipelines"]

    def sessions(self):
        return self._subclients["sessions"]

    def federation(self):
        return self._subclients["federation"]

    def query(self, q):
        self.calls.append(("query", q))
        return self._subclients["query_result"]

    def preview_count(self, q):
        self.calls.append(("preview_count", q))
        return self._subclients.get("preview_count", 0)


@pytest.fixture
def fake_client():
    return FakeWorkbenchClient()


@pytest.fixture
def connected(fake_client):
    """A ConnectionManager pre-loaded with a fake client."""
    cm = ConnectionManager()
    cm._inject(fake_client)
    return cm, fake_client
