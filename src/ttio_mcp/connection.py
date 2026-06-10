# src/ttio_mcp/connection.py
"""Single-session connection manager around ttio.workbench.WorkbenchClient."""
from __future__ import annotations

from typing import Any

import ttio
from ttio import BearerAuth, PasswordTotpAuth

from ttio_mcp.errors import ToolError


class ConnectionManager:
    """Owns at most one authenticated WorkbenchClient.

    Not threading-aware: the MCP server runs single-process, one event
    loop. Tokens live in memory only.
    """

    def __init__(self) -> None:
        self._client: Any | None = None

    # --- test / internal injection -------------------------------------
    def _inject(self, client: Any) -> None:
        self._client = client

    # --- lifecycle -----------------------------------------------------
    def login_password(self, url: str, username: str, password: str, totp: str) -> dict[str, Any]:
        self._client = ttio.connect(url, auth=PasswordTotpAuth(username, password, totp))
        return self.status()

    def login_token(self, url: str, token: str, username: str | None = None) -> dict[str, Any]:
        self._client = ttio.connect(url, auth=BearerAuth(token, username or "token-user"))
        return self.status()

    def logout(self) -> None:
        self._client = None

    # --- access --------------------------------------------------------
    def require_client(self) -> Any:
        if self._client is None:
            raise ToolError("Not connected. Call ttio_login (or set TTIO_WB_URL + TTIO_WB_TOKEN).")
        session = getattr(self._client, "session", None)
        if session is not None and getattr(session, "expired", False):
            raise ToolError("Session expired. Call ttio_login again (API-key tokens do not expire).")
        return self._client

    def status(self) -> dict[str, Any]:
        if self._client is None:
            return {"connected": False}
        s = getattr(self._client, "session", None)
        return {
            "connected": True,
            "username": getattr(s, "username", None),
            "projects": list(getattr(s, "projects", ()) or ()),
            "capabilities": sorted(getattr(s, "capabilities", ()) or ()),
            "expired": bool(getattr(s, "expired", False)),
        }
