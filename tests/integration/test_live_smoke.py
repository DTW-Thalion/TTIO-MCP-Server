# tests/integration/test_live_smoke.py
"""Live smoke test against a running tti-workbench-server.

Skipped unless TTIO_MCP_LIVE=1 and TTIO_WB_URL + credentials are set.
Launch the daemon + bootstrap admin per the server repo's smoke scripts first.
"""
import os

import pytest

LIVE = os.environ.get("TTIO_MCP_LIVE") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="set TTIO_MCP_LIVE=1 to run live")


def test_login_and_list_containers():
    import ttio
    url = os.environ["TTIO_WB_URL"]
    token = os.environ.get("TTIO_WB_TOKEN")
    if token:
        client = ttio.connect(url, auth=ttio.BearerAuth(token, os.environ.get("TTIO_WB_USERNAME", "u")))
    else:
        client = ttio.connect(url, auth=ttio.PasswordTotpAuth(
            os.environ["TTIO_WB_USERNAME"], os.environ["TTIO_WB_PASSWORD"], os.environ["TTIO_WB_TOTP"]))
    page = client.containers().list(limit=5)
    assert hasattr(page, "containers")
