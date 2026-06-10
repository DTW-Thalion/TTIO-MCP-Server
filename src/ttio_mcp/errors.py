# src/ttio_mcp/errors.py
"""Translate ttio/workbench exceptions into clean, actionable tool messages."""
from __future__ import annotations

from ttio.workbench._http import WorkbenchHttpError
from ttio.workbench.auth import (
    AccountDisabled,
    InvalidCredentials,
    RateLimitExceeded,
    WorkbenchAuthError,
)


class ToolError(Exception):
    """Raised by tools to signal a clean, user-facing failure."""


def to_tool_error(exc: Exception) -> str:
    """Return a single-line, user-facing message for *exc*."""
    if isinstance(exc, InvalidCredentials):
        return "Invalid credentials (401): username, password, or TOTP was rejected."
    if isinstance(exc, AccountDisabled):
        return "Account disabled (423): contact a workbench administrator."
    if isinstance(exc, RateLimitExceeded):
        retry = getattr(exc, "retry_after_seconds", None)
        tail = f" Retry after {retry}s." if retry else ""
        return f"Rate limited (429).{tail}"
    if isinstance(exc, WorkbenchHttpError):
        status = getattr(exc, "status", "?")
        body = getattr(exc, "body", None)
        detail = ""
        if isinstance(body, dict):
            detail = str(body.get("error") or body.get("reason") or "")
        if status == 403:
            return f"Forbidden (403): {detail or 'missing capability for this operation.'}"
        return f"Server error ({status}): {detail or exc}".rstrip()
    if isinstance(exc, WorkbenchAuthError):
        return f"Authentication error: {exc}"
    return f"Error: {exc}"
