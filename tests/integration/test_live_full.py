"""Full live matrix: exercise every ttio-mcp tool (and every transfer mode) end
to end against a running tti-workbench-server, through the MCP protocol.

Complements test_live_smoke.py. Most tests provision their own server state via
the bootstrap-admin SDK (project membership + pipeline registration are admin and
not MCP tools), so they require TTIO_WB_BOOTSTRAP_STAGING in addition to the base
TTIO_MCP_LIVE config. Per-test prerequisites skip cleanly when unmet:

  TTIO_MCP_LIVE=1, TTIO_WB_URL                         base (else module skips)
  TTIO_WB_BOOTSTRAP_STAGING                            admin setup (most tests)
  TTIO_MCP_LIVE_TIO                                    a .tio fixture (container/transfer/data)
  TTIO_MCP_LIVE_PROJECT                                project name (default 'demo')
  TTIO_MCP_LIVE_KEK_ID                                 resolvable server KEK id (server-kek test;
                                                       needs the HSM-backed daemon)
  pqc test additionally needs liboqs (`pip install ttio[pqc]`).
"""
import asyncio
import base64
import os
import secrets
import uuid

import pytest

from tests.integration._live_mcp import (
    LIVE,
    admin_in_project,
    bootstrap_creds,
    call_tool,
    mcp_session,
    register_shell_pipeline,
    server_url,
)

pytestmark = pytest.mark.skipif(not LIVE, reason="set TTIO_MCP_LIVE=1 to run live")


@pytest.fixture
def url():
    return server_url()


@pytest.fixture
def project():
    return os.environ.get("TTIO_MCP_LIVE_PROJECT", "demo")


@pytest.fixture
def admin(url, project):
    """Bootstrap-admin SDK client that belongs to `project` (skips without staging)."""
    return admin_in_project(url, project)


@pytest.fixture
def creds(admin):
    """(token, username) for an authenticated, project-scoped admin."""
    return admin.session.token, admin.session.username


@pytest.fixture
def tio():
    path = os.environ.get("TTIO_MCP_LIVE_TIO")
    if not path:
        pytest.skip("set TTIO_MCP_LIVE_TIO to a .tio fixture")
    return path


async def _upload(session, project, tio, *, mode="plain", **kw):
    uri = f"uri:tio:full-{uuid.uuid4().hex[:8]}"
    out = await call_tool(session, "ttio_upload", project=project, container_uri=uri,
                          path=tio, mode=mode, **kw)
    assert out.get("container_uri") == uri and "error" not in out, out
    return uri


# --------------------------------------------------------------------------- #
# Auth lifecycle
# --------------------------------------------------------------------------- #
async def test_auth_lifecycle(url, creds):
    c = bootstrap_creds()
    from ttio.workbench.auth import current_totp
    token, username = creds
    async with mcp_session(url, token, username) as session:
        who = await call_tool(session, "ttio_whoami")
        assert who.get("connected") is True, who
        login = await call_tool(session, "ttio_login", username=c["username"],
                               password=c["password"], totp=current_totp(c["totp_secret_base32"]))
        assert login.get("connected") is True and login.get("username") == c["username"], login
        await call_tool(session, "ttio_logout")
        status = await call_tool(session, "ttio_connection_status")
        assert status.get("connected") is False, status


# --------------------------------------------------------------------------- #
# Containers (inspection)
# --------------------------------------------------------------------------- #
async def test_container_inspection(url, creds, project, tio):
    token, username = creds
    async with mcp_session(url, token, username) as session:
        uri = await _upload(session, project, tio)
        got = await call_tool(session, "ttio_container_get", uri=uri)
        assert got.get("uri") == uri, got
        layers = await call_tool(session, "ttio_container_layers", uri=uri)
        assert "layers" in layers, layers
        manifest = await call_tool(session, "ttio_container_manifest", uri=uri)
        assert "error" not in manifest and manifest.get("uri") == uri, manifest


# --------------------------------------------------------------------------- #
# Cohorts
# --------------------------------------------------------------------------- #
async def test_cohort_queries(url, creds):
    token, username = creds
    async with mcp_session(url, token, username) as session:
        count = await call_tool(session, "ttio_cohort_preview_count", select="containers")
        assert "count" in count, count
        for select in ("containers", "subjects", "samples"):
            out = await call_tool(session, "ttio_cohort_query", select=select)
            assert "rows" in out, (select, out)


# --------------------------------------------------------------------------- #
# Jobs + pipelines
# --------------------------------------------------------------------------- #
async def test_jobs_lifecycle(url, creds, admin, project):
    token, username = creds
    pid_fast = register_shell_pipeline(admin, project, definition="echo hi && sleep 0.1")
    pid_slow = register_shell_pipeline(admin, project, definition="sleep 30")
    async with mcp_session(url, token, username) as session:
        plist = await call_tool(session, "ttio_pipelines_list")
        assert any(p.get("pipeline_id") == pid_fast for p in plist.get("pipelines", [])), plist
        pget = await call_tool(session, "ttio_pipeline_get", pipeline_id=pid_fast)
        assert pget.get("pipeline_id") == pid_fast, pget

        job = await call_tool(session, "ttio_job_submit", pipeline_id=pid_fast, inputs={}, params={})
        job_id = job.get("job_id")
        assert job_id, job
        jlist = await call_tool(session, "ttio_jobs_list")
        assert any(j.get("job_id") == job_id for j in jlist.get("jobs", [])), jlist
        assert (await call_tool(session, "ttio_job_get", job_id=job_id)).get("job_id") == job_id
        await asyncio.sleep(1.0)  # let the fast job stream + terminate
        events = await call_tool(session, "ttio_job_events", job_id=job_id, max_events=5)
        assert len(events.get("events", [])) >= 1, events

        slow = await call_tool(session, "ttio_job_submit", pipeline_id=pid_slow, inputs={}, params={})
        cancel = await call_tool(session, "ttio_job_cancel", job_id=slow.get("job_id"))
        assert cancel.get("cancelled") == slow.get("job_id"), cancel


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
async def test_sessions_lifecycle(url, creds, project):
    token, username = creds
    async with mcp_session(url, token, username) as session:
        created = await call_tool(session, "ttio_session_create", project=project, engine_pin="shell")
        sid = created.get("session_id")
        assert sid, created
        slist = await call_tool(session, "ttio_sessions_list")
        assert any(s.get("session_id") == sid for s in slist.get("sessions", [])), slist
        assert (await call_tool(session, "ttio_session_get", session_id=sid)).get("session_id") == sid
        attach = await call_tool(session, "ttio_session_attach_url", session_id=sid)
        assert attach.get("attach_url"), attach
        term = await call_tool(session, "ttio_session_terminate", session_id=sid)
        assert term.get("terminated") == sid, term


# --------------------------------------------------------------------------- #
# Transfers — byok / server-kek / pqc
# --------------------------------------------------------------------------- #
async def test_byok_round_trip(url, creds, project, tio, tmp_path):
    token, username = creds
    key = secrets.token_bytes(32).hex()
    out = str(tmp_path / "byok.tio")
    async with mcp_session(url, token, username) as session:
        uri = await _upload(session, project, tio, mode="byok", key=key)
        dn = await call_tool(session, "ttio_download", container_uri=uri, out_path=out, mode="byok", key=key)
        assert dn.get("out_path") == out and os.path.exists(out), dn
        summary = await call_tool(session, "ttio_dataset_summary", path=out)
        assert summary.get("title") is not None and summary.get("runs"), summary


async def test_server_kek_round_trip(url, creds, project, tio, tmp_path):
    kek_id = os.environ.get("TTIO_MCP_LIVE_KEK_ID")
    if not kek_id:
        pytest.skip("set TTIO_MCP_LIVE_KEK_ID (resolvable server KEK; needs the HSM-backed daemon)")
    token, username = creds
    out = str(tmp_path / "serverkek.tio")
    async with mcp_session(url, token, username) as session:
        uri = await _upload(session, project, tio, mode="server-kek", kek_id=kek_id)
        dn = await call_tool(session, "ttio_download", container_uri=uri, out_path=out, mode="server-kek")
        assert dn.get("out_path") == out and os.path.exists(out), dn
        summary = await call_tool(session, "ttio_dataset_summary", path=out)
        assert summary.get("title") is not None and summary.get("runs"), summary


async def test_pqc_round_trip(url, creds, project, tio, tmp_path):
    try:
        from ttio.workbench.pqc import kem_keygen
    except Exception:  # noqa: BLE001 - liboqs not installed
        pytest.skip("PQC needs liboqs (pip install ttio[pqc])")
    kp = kem_keygen()
    pub = base64.b64encode(kp.public_key).decode()
    priv = base64.b64encode(kp.private_key).decode()
    token, username = creds
    out = str(tmp_path / "pqc.tio")
    async with mcp_session(url, token, username) as session:
        uri = await _upload(session, project, tio, mode="pqc", recipient_public_key=pub, preview=True)
        dn = await call_tool(session, "ttio_download", container_uri=uri, out_path=out,
                            mode="pqc", recipient_private_key=priv, preview=True)
        assert dn.get("out_path") == out and os.path.exists(out), dn
        summary = await call_tool(session, "ttio_dataset_summary", path=out)
        assert summary.get("title") is not None and summary.get("runs"), summary


# --------------------------------------------------------------------------- #
# Data extraction — every selector + every export format
# --------------------------------------------------------------------------- #
async def test_dataset_read_all_selectors(url, creds, tio):
    token, username = creds
    async with mcp_session(url, token, username) as session:
        summary = await call_tool(session, "ttio_dataset_summary", path=tio)
        assert summary.get("title") is not None, summary
        run = next(iter(summary.get("runs", {"run_0001": {}})))
        for what in ("runs", "spectrum", "signal", "subjects", "samples", "images",
                     "identifications", "quantifications", "provenance"):
            kw = {"path": tio, "what": what}
            if what in ("spectrum", "signal"):
                kw.update(run=run, index=0)
            if what == "signal":
                kw["signal"] = "intensity"
            out = await call_tool(session, "ttio_dataset_read", **kw)
            assert "error" not in out, (what, out)


async def test_dataset_export_all_formats(url, creds, tio, tmp_path):
    token, username = creds
    async with mcp_session(url, token, username) as session:
        summary = await call_tool(session, "ttio_dataset_summary", path=tio)
        run = next(iter(summary.get("runs", {"run_0001": {}})))
        for fmt in ("json", "csv", "parquet"):
            out = await call_tool(session, "ttio_dataset_export", path=tio, run=run, index=0,
                                 out_dir=str(tmp_path), fmt=fmt)
            assert out.get("export_path", "").endswith(fmt) and os.path.exists(out["export_path"]), out
