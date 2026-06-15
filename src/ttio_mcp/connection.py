# src/ttio_mcp/connection.py
"""Per-session connection registry around ttio.workbench.WorkbenchClient."""
from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import ttio
from ttio import BearerAuth, PasswordTotpAuth

from ttio_mcp.errors import ToolError

_DEFAULT_KEY = "__default__"
_MAX_SESSIONS = 256


def _session_expired(session: Any) -> bool:
    """True only if the session has a real expiry that has passed.

    ``BearerAuth`` / API-key sessions are synthesized with ``expires_at == 0``
    as a "never expires" sentinel; the SDK's ``.expired`` would otherwise read
    epoch 0 as long-past. Treat a falsy ``expires_at`` as non-expiring.
    """
    if session is None:
        return False
    if not getattr(session, "expires_at", 0):
        return False
    return bool(getattr(session, "expired", False))


class ConnectionManager:
    """Owns one authenticated WorkbenchClient PER MCP session.

    The current session is identified by an injected resolver; outside a request
    (stdio, tests, startup) it falls back to a single default key, so behaviour
    matches the old single-session manager. Tokens live in memory only. The
    server runs one event loop, but a lock guards the registry against
    interleaved access.
    """

    def __init__(self, max_sessions: int = _MAX_SESSIONS) -> None:
        self._clients: OrderedDict[Any, Any] = OrderedDict()
        self._lock = threading.RLock()
        self._resolver: Callable[[], Any] | None = None
        self._service: tuple[str, str, str | None] | None = None
        self._oauth: object | None = None
        self._max_sessions = max_sessions

    # --- wiring --------------------------------------------------------
    def bind_session_resolver(self, resolver: Callable[[], Any]) -> None:
        self._resolver = resolver

    def enable_service_autoconnect(
        self, url: str, token: str, username: str | None = None
    ) -> None:
        self._service = (url, token, username)

    def enable_oauth(self, config: object) -> None:
        """Store OAuth/Keycloak config for per-session token-exchange connect.

        Task 5 will implement _maybe_oauth_connect() on top of this.  For now
        this is a minimal setter so build_app() can wire the config without
        calling a non-existent method.
        """
        self._oauth = config

    def _key(self) -> Any:
        if self._resolver is not None:
            try:
                k = self._resolver()
                if k is not None:
                    return k
            except Exception:
                pass
        return _DEFAULT_KEY

    def _store(self, key: Any, client: Any) -> None:
        with self._lock:
            self._clients[key] = client
            self._clients.move_to_end(key)
            while len(self._clients) > self._max_sessions:
                # Evict least-recently-used. The ttio WorkbenchClient exposes no
                # close(); dropping the reference lets GC reclaim it.
                self._clients.popitem(last=False)

    # --- test / internal injection -------------------------------------
    def _inject(self, client: Any) -> None:
        self._store(self._key(), client)

    # --- lifecycle -----------------------------------------------------
    def login_password(self, url: str, username: str, password: str, totp: str) -> dict[str, Any]:
        client = ttio.connect(url, auth=PasswordTotpAuth(username, password, totp))
        self._store(self._key(), client)
        return self.status()

    def login_token(self, url: str, token: str, username: str | None = None) -> dict[str, Any]:
        client = ttio.connect(url, auth=BearerAuth(token, username or "token-user"))
        self._store(self._key(), client)
        return self.status()

    def logout(self) -> None:
        with self._lock:
            self._clients.pop(self._key(), None)

    # --- access --------------------------------------------------------
    def require_client(self) -> Any:
        key = self._key()
        with self._lock:
            client = self._clients.get(key)
            if client is not None:
                self._clients.move_to_end(key)
        if client is None:
            client = self._maybe_service_connect(key)
        if client is None:
            raise ToolError(
                "Not connected. Call ttio_login (or set TTIO_WB_URL + TTIO_WB_TOKEN)."
            )
        if _session_expired(getattr(client, "session", None)):
            raise ToolError(
                "Session expired. Call ttio_login again (API-key tokens do not expire)."
            )
        return client

    def _maybe_service_connect(self, key: Any) -> Any:
        if self._service is None:
            return None
        url, token, username = self._service
        try:
            client = ttio.connect(url, auth=BearerAuth(token, username or "token-user"))
        except Exception:
            return None
        self._store(key, client)
        return client

    def status(self) -> dict[str, Any]:
        with self._lock:
            client = self._clients.get(self._key())
        if client is None:
            return {"connected": False}
        s = getattr(client, "session", None)
        return {
            "connected": True,
            "username": getattr(s, "username", None),
            "projects": list(getattr(s, "projects", ()) or ()),
            "capabilities": sorted(getattr(s, "capabilities", ()) or ()),
            "expired": _session_expired(s),
        }
