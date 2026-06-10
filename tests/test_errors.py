# tests/test_errors.py
import pytest
from ttio.workbench._http import WorkbenchHttpError
from ttio.workbench.auth import AccountDisabled, InvalidCredentials, RateLimitExceeded

from ttio_mcp.errors import ToolError, to_tool_error


def test_invalid_credentials_maps():
    msg = to_tool_error(InvalidCredentials("bad"))
    assert "credential" in msg.lower()


def test_rate_limit_includes_retry_after():
    err = RateLimitExceeded("slow down", retry_after_seconds=12)
    msg = to_tool_error(err)
    assert "12" in msg


def test_http_403_names_capability():
    err = WorkbenchHttpError("forbidden", status=403, body={"error": "missing capability jobs.submit"})
    msg = to_tool_error(err)
    assert "403" in msg
    assert "jobs.submit" in msg


def test_account_disabled():
    assert "disabled" in to_tool_error(AccountDisabled("x")).lower()


def test_tool_error_is_exception():
    with pytest.raises(ToolError):
        raise ToolError("boom")
