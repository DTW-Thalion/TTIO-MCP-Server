"""Helpers for the opt-in live MCP integration tests.

These drive the real ``ttio-mcp`` server (as a stdio subprocess) against a
running ``tti-workbench-server``. Heavy imports (``ttio``, ``mcp``) are done
lazily inside the functions so plain test collection never requires a daemon.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys

import pytest

LIVE = os.environ.get("TTIO_MCP_LIVE") == "1"


def server_url() -> str:
    """The workbench URL from TTIO_WB_URL, or skip with a clear reason."""
    url = os.environ.get("TTIO_WB_URL")
    if not url:
        pytest.skip("TTIO_MCP_LIVE set but TTIO_WB_URL is not")
    return url


def obtain_token(url: str) -> tuple[str, str]:
    """Return ``(token, username)`` from whichever credential source is set.

    Precedence:
      1. ``TTIO_WB_TOKEN`` (a ``ttiowbk_``/``ttiowbs_`` token) — used directly.
      2. ``TTIO_WB_BOOTSTRAP_STAGING`` — a staging dir holding
         ``bootstrap-credentials.json``; logs in via ``BootstrapAdminAuth``.
      3. ``TTIO_WB_USERNAME`` + ``TTIO_WB_PASSWORD`` + ``TTIO_WB_TOTP_SECRET``
         — logs in with a freshly computed TOTP.
    Skips with guidance if none are configured.
    """
    import ttio

    token = os.environ.get("TTIO_WB_TOKEN")
    if token:
        return token, os.environ.get("TTIO_WB_USERNAME", "token-user")

    staging = os.environ.get("TTIO_WB_BOOTSTRAP_STAGING")
    if staging:
        client = ttio.connect(url, auth=ttio.BootstrapAdminAuth(staging_root=staging))
        return client.session.token, client.session.username

    user = os.environ.get("TTIO_WB_USERNAME")
    pw = os.environ.get("TTIO_WB_PASSWORD")
    secret = os.environ.get("TTIO_WB_TOTP_SECRET")
    if user and pw and secret:
        from ttio.workbench.auth import current_totp
        client = ttio.connect(url, auth=ttio.PasswordTotpAuth(user, pw, current_totp(secret)))
        return client.session.token, client.session.username

    pytest.skip(
        "no workbench credentials: set TTIO_WB_TOKEN, or TTIO_WB_BOOTSTRAP_STAGING, "
        "or TTIO_WB_USERNAME+TTIO_WB_PASSWORD+TTIO_WB_TOTP_SECRET"
    )


@contextlib.asynccontextmanager
async def mcp_session(url: str, token: str, username: str):
    """Launch ``ttio-mcp`` as a stdio subprocess (headless token auto-connect)
    and yield an initialized MCP ``ClientSession``."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env["TTIO_WB_URL"] = url
    env["TTIO_WB_TOKEN"] = token
    env["TTIO_WB_USERNAME"] = username
    # Run the server module with the same interpreter that runs the tests.
    params = StdioServerParameters(command=sys.executable, args=["-m", "ttio_mcp.server"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call_tool(session, name: str, **args) -> dict:
    """Call an MCP tool and return its result decoded as a dict."""
    res = await session.call_tool(name, args)
    txt = res.content[0].text if res.content else "{}"
    try:
        return json.loads(txt)
    except (ValueError, TypeError):
        return {"_raw": txt}


# --- admin setup helpers (require TTIO_WB_BOOTSTRAP_STAGING) -----------------
# Jobs / sessions / encrypted-transfer tests need an identity that belongs to a
# project (and, for jobs, a registered pipeline). Pipeline registration is admin
# and intentionally not an MCP tool, so the suite provisions it out-of-band via
# the bootstrap-admin SDK. These skip cleanly when no admin staging is provided.

def bootstrap_creds() -> dict:
    """The bootstrap-admin credentials dict, or skip if no staging dir."""
    staging = os.environ.get("TTIO_WB_BOOTSTRAP_STAGING")
    if not staging:
        pytest.skip("needs TTIO_WB_BOOTSTRAP_STAGING (bootstrap-admin credentials)")
    with open(os.path.join(staging, "bootstrap-credentials.json")) as fh:
        return json.load(fh)


def admin_in_project(url: str, project: str):
    """Return a bootstrap-admin SDK client that is a member of *project*.

    Grants membership via PATCH /v1/auth/users/{uid} then re-logs-in so the
    session carries the project. Skips if no admin staging is configured.
    """
    import ttio

    staging = os.environ.get("TTIO_WB_BOOTSTRAP_STAGING")
    if not staging:
        pytest.skip("needs TTIO_WB_BOOTSTRAP_STAGING (admin) to grant project / register pipelines")
    client = ttio.connect(url, auth=ttio.BootstrapAdminAuth(staging_root=staging))
    if project not in (client.session.projects or ()):
        import urllib.request
        http = f"{client.http_scheme}://{client.host}:{client.port}"
        urllib.request.urlopen(urllib.request.Request(
            f"{http}/v1/auth/users/{client.session.user_id}", method="PATCH",
            data=json.dumps({"projects": [project]}).encode(),
            headers={"Authorization": f"Bearer {client.session.token}",
                     "Content-Type": "application/json"},
        )).read()
        client = ttio.connect(url, auth=ttio.BootstrapAdminAuth(staging_root=staging))
    return client


def register_shell_pipeline(client, project: str, *, definition: str = "echo ok && sleep 0.1") -> str:
    """Register a throwaway shell pipeline via the admin SDK; return its id."""
    import secrets
    return client.pipelines().register(
        identifier=f"e2e-{secrets.token_hex(3)}", version="1.0.0", project=project,
        engine_pin="shell", definition=definition, inputs_schema={}, outputs_schema={},
    ).pipeline_id
