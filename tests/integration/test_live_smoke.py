"""Opt-in live integration: drive the ttio-mcp server (stdio) against a running
tti-workbench-server.

These tests launch the MCP server as a real subprocess and exercise it through
an MCP client — the full chain ``MCP client -> ttio-mcp -> ttio SDK -> daemon``.
They never run in unit CI; a real daemon must be reachable.

Enable and configure via environment:

  TTIO_MCP_LIVE=1                         enable (else the whole module skips)
  TTIO_WB_URL=ws://host:18443/transport   the daemon URL (required)

  one credential source (see _live_mcp.obtain_token):
    TTIO_WB_TOKEN                          a ttiowbk_/ttiowbs_ token, OR
    TTIO_WB_BOOTSTRAP_STAGING             staging dir with bootstrap-credentials.json, OR
    TTIO_WB_USERNAME + TTIO_WB_PASSWORD + TTIO_WB_TOTP_SECRET

  optional data round-trip (upload/download):
    TTIO_MCP_LIVE_TIO                      path to a .tio fixture to upload
    TTIO_MCP_LIVE_PROJECT                 project name (default 'demo')

Launch the daemon first (see the tti-workbench-server repo's scripts/), e.g.:
    TtioWBServer --config conf.json   # writes <staging>/bootstrap-credentials.json
then:
    TTIO_MCP_LIVE=1 TTIO_WB_URL=ws://127.0.0.1:18443/transport \
    TTIO_WB_BOOTSTRAP_STAGING=<staging> \
    TTIO_MCP_LIVE_TIO=/path/to/sample.tio \
    pytest tests/integration -v
"""
import os
import uuid

import pytest

from tests.integration._live_mcp import (
    LIVE,
    call_tool,
    mcp_session,
    obtain_token,
    server_url,
)

pytestmark = pytest.mark.skipif(not LIVE, reason="set TTIO_MCP_LIVE=1 to run live")

# Substrings that must never appear in an exposed tool name (admin / destructive).
ADMIN_MARKERS = ("delete", "user", "group", "dashboard", "rewrap", "kms")
EXPECTED_TOOL_COUNT = 28


async def test_mcp_read_surface():
    """Tool surface + auth + all read/list tools round-trip through the daemon."""
    url = server_url()
    token, username = obtain_token(url)
    async with mcp_session(url, token, username) as session:
        names = sorted(t.name for t in (await session.list_tools()).tools)
        assert len(names) == EXPECTED_TOOL_COUNT, names
        leaked = [n for n in names if any(m in n for m in ADMIN_MARKERS)]
        assert not leaked, f"admin/destructive tools exposed: {leaked}"

        status = await call_tool(session, "ttio_connection_status")
        assert status.get("connected") is True, status

        for tool, key in (
            ("ttio_containers_list", "containers"),
            ("ttio_pipelines_list", "pipelines"),
            ("ttio_sessions_list", "sessions"),
            ("ttio_federation_peers", "peers"),
        ):
            out = await call_tool(session, tool)
            assert key in out, (tool, out)

        count = await call_tool(session, "ttio_cohort_preview_count", select="containers")
        assert "count" in count, count


async def test_mcp_data_round_trip(tmp_path):
    """Upload a real .tio, browse it on the daemon, download it, and read it.

    Skipped unless TTIO_MCP_LIVE_TIO points at a .tio fixture.
    """
    tio = os.environ.get("TTIO_MCP_LIVE_TIO")
    if not tio:
        pytest.skip("set TTIO_MCP_LIVE_TIO to a .tio fixture to run the upload/download round-trip")

    url = server_url()
    token, username = obtain_token(url)
    project = os.environ.get("TTIO_MCP_LIVE_PROJECT", "demo")
    uri = f"uri:tio:mcp-live-{uuid.uuid4().hex[:8]}"
    out_tis = str(tmp_path / "download.tis")

    async with mcp_session(url, token, username) as session:
        up = await call_tool(session, "ttio_upload", project=project,
                             container_uri=uri, path=tio, mode="plain")
        assert up.get("container_uri") == uri and "error" not in up, up

        listing = await call_tool(session, "ttio_containers_list")
        assert uri in [c.get("uri") for c in listing.get("containers", [])], listing

        manifest = await call_tool(session, "ttio_container_manifest", uri=uri)
        assert "error" not in manifest, manifest

        dn = await call_tool(session, "ttio_download", container_uri=uri,
                            out_path=out_tis, mode="plain")
        assert dn.get("bytes", 0) > 0, dn

        # Data-extraction tools operate on the local .tio fixture.
        summary = await call_tool(session, "ttio_dataset_summary", path=tio)
        assert summary.get("runs"), summary
        run = next(iter(summary["runs"]))
        spectrum = await call_tool(session, "ttio_dataset_read", path=tio,
                                  what="spectrum", run=run, index=0, top_n=3)
        assert spectrum.get("mz", {}).get("count", 0) > 0, spectrum
