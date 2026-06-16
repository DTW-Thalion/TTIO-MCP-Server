# src/ttio_mcp/server.py
"""FastMCP entry point for ttio-mcp (tti-workbench-server client)."""
from __future__ import annotations

import asyncio
import io
import os

import anyio
from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager

# Process-wide singletons.
CONN = ConnectionManager()
CONFIG = Config.from_env()


def build_app() -> FastMCP:
    if CONFIG.oauth_enabled:
        # Fail fast on a half-configured OAuth setup: oauth_enabled is true as
        # soon as the issuer is set, but the verifier + token exchange (Task 5)
        # also need these fields. Without this guard a missing field surfaces
        # only as mysterious runtime 401s.
        _required = {
            "TTIO_MCP_OAUTH_JWKS_URL": CONFIG.oauth_jwks_url,
            "TTIO_MCP_OAUTH_RESOURCE_URL": CONFIG.oauth_resource_url,
            "TTIO_MCP_OAUTH_TOKEN_URL": CONFIG.oauth_token_url,
            "TTIO_MCP_OAUTH_CLIENT_ID": CONFIG.oauth_client_id,
            "TTIO_MCP_OAUTH_CLIENT_SECRET": CONFIG.oauth_client_secret,
        }
        _missing = [name for name, val in _required.items() if not val]
        if _missing:
            raise ValueError(
                "OAuth is enabled (TTIO_MCP_OAUTH_ISSUER set) but these required "
                f"settings are unset: {', '.join(_missing)}."
            )
        # Narrow the Optional[str] fields for the type checker (the guard above
        # already guarantees they are set).
        assert CONFIG.oauth_issuer and CONFIG.oauth_jwks_url and CONFIG.oauth_resource_url

        from mcp.server.auth.settings import AuthSettings

        from ttio_mcp.oauth import KeycloakTokenVerifier

        verifier = KeycloakTokenVerifier(
            jwks_url=CONFIG.oauth_jwks_url,
            issuer=CONFIG.oauth_issuer,
            audience=CONFIG.oauth_audience,
            algorithms=list(CONFIG.oauth_allowed_algs),
            required_scopes=list(CONFIG.oauth_required_scopes),
        )
        app = FastMCP(
            "ttio-mcp",
            host=CONFIG.http_host,
            port=CONFIG.http_port,
            streamable_http_path=CONFIG.http_path,
            token_verifier=verifier,
            auth=AuthSettings(
                # pydantic coerces these str URLs to AnyHttpUrl at validation.
                issuer_url=CONFIG.oauth_issuer,  # type: ignore[arg-type]
                resource_server_url=CONFIG.oauth_resource_url,  # type: ignore[arg-type]
                required_scopes=list(CONFIG.oauth_required_scopes),
            ),
        )
        CONN.enable_oauth(CONFIG)  # stub in ConnectionManager; Task 5 adds the connect logic
    else:
        app = FastMCP(
            "ttio-mcp",
            host=CONFIG.http_host,
            port=CONFIG.http_port,
            streamable_http_path=CONFIG.http_path,
        )

    from starlette.responses import JSONResponse

    async def _healthz(_request):
        return JSONResponse({"status": "ok"})

    app.custom_route("/healthz", methods=["GET"])(_healthz)

    from ttio_mcp.tools import auth as auth_tools

    auth_tools.register(app, CONN, CONFIG)
    from ttio_mcp.tools import containers as containers_tools
    containers_tools.register(app, CONN, CONFIG)
    from ttio_mcp.tools import cohorts as cohorts_tools
    cohorts_tools.register(app, CONN, CONFIG)
    from ttio_mcp.tools import jobs as jobs_tools
    jobs_tools.register(app, CONN, CONFIG)
    from ttio_mcp.tools import sessions as sessions_tools
    sessions_tools.register(app, CONN, CONFIG)
    from ttio_mcp.tools import transfers as transfers_tools
    transfers_tools.register(app, CONN, CONFIG)
    from ttio_mcp.tools import data as data_tools
    data_tools.register(app, CONN, CONFIG)
    CONN.bind_session_resolver(lambda: _session_key(app))
    _maybe_autoconnect()
    return app


def _session_key(app: FastMCP):
    """Stable per-connection key, or None outside a request (-> default key).

    ``get_context()`` raises outside an active request; we treat that as "no
    session context" so the ConnectionManager uses its single default slot
    (stdio, tests, startup).
    """
    try:
        return id(app.get_context().session)
    except Exception:
        return None


def _maybe_autoconnect() -> None:
    """Enable lazy per-session service-account auto-connect when configured.

    Each MCP session connects its own client on first use (see
    ConnectionManager.require_client), rather than one shared client created
    eagerly at startup.
    """
    if CONFIG.url and CONFIG.token:
        CONN.enable_service_autoconnect(CONFIG.url, CONFIG.token, CONFIG.username)


def _reserve_stdout_for_protocol() -> io.TextIOWrapper:
    """Reserve the real stdout (fd 1) exclusively for the MCP JSON-RPC stream.

    An stdio MCP server frames its protocol on stdout, so ANY stray write to
    stdout corrupts it — including C-level writes that Python's
    ``redirect_stdout`` cannot intercept (e.g. liboqs prints a banner to fd 1 on
    import when a PQC transfer runs). We dup the real stdout, then point fd 1 at
    stderr so every other write (``print``, C-level fd-1 writes) lands on stderr.
    The returned stream — over the saved real stdout — is handed to the MCP
    transport so only protocol frames reach the client.
    """
    saved = os.dup(1)
    os.dup2(2, 1)  # fd 1 -> stderr; protects the protocol from stray stdout writes
    return io.TextIOWrapper(os.fdopen(saved, "wb", buffering=0), encoding="utf-8", write_through=True)


async def _serve() -> None:
    from mcp.server.stdio import stdio_server

    protocol_stdout = _reserve_stdout_for_protocol()
    app = build_app()
    srv = app._mcp_server
    async with stdio_server(stdout=anyio.wrap_file(protocol_stdout)) as (read_stream, write_stream):
        await srv.run(read_stream, write_stream, srv.create_initialization_options())


def _serve_http() -> None:
    """Serve over streamable-HTTP (uvicorn + the MCP session manager).

    No stdout protection is needed here — HTTP does not frame the protocol on
    stdout. Host/port/path come from Config via build_app().
    """
    build_app().run(transport="streamable-http")


def main() -> None:
    if CONFIG.transport == "http":
        _serve_http()
    else:
        asyncio.run(_serve())


if __name__ == "__main__":
    main()
