import dataclasses

import pytest
from starlette.testclient import TestClient

import ttio_mcp.server as server


def test_build_app_applies_http_settings(monkeypatch):
    custom = dataclasses.replace(
        server.CONFIG, http_host="0.0.0.0", http_port=9999, http_path="/ttio"
    )
    monkeypatch.setattr(server, "CONFIG", custom)
    app = server.build_app()
    assert app.settings.host == "0.0.0.0"
    assert app.settings.port == 9999
    assert app.settings.streamable_http_path == "/ttio"


def test_healthz_route_registered():
    app = server.build_app()
    with TestClient(app.streamable_http_app()) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_main_dispatches_http(monkeypatch):
    monkeypatch.setattr(server, "CONFIG", dataclasses.replace(server.CONFIG, transport="http"))
    monkeypatch.setattr(server, "_maybe_autoconnect", lambda: None)
    captured = {}
    monkeypatch.setattr(
        server.FastMCP, "run",
        lambda self, transport=None, **kw: captured.setdefault("transport", transport),
    )
    server.main()
    assert captured["transport"] == "streamable-http"


def test_main_dispatches_stdio(monkeypatch):
    monkeypatch.setattr(server, "CONFIG", dataclasses.replace(server.CONFIG, transport="stdio"))
    monkeypatch.setattr(server, "_maybe_autoconnect", lambda: None)
    monkeypatch.setattr(server, "_serve", lambda: "coro-stub")
    ran = {}
    monkeypatch.setattr(server.asyncio, "run", lambda coro: ran.setdefault("ran", coro))
    server.main()
    assert ran.get("ran") == "coro-stub"


def test_session_key_outside_request_is_none():
    # get_context() raises outside a request; _session_key swallows that -> None,
    # so ConnectionManager falls back to its default key (stdio/tests/startup).
    assert server._session_key(server.build_app()) is None


def test_maybe_autoconnect_enables_lazy_service(monkeypatch):
    monkeypatch.setattr(
        server, "CONFIG",
        dataclasses.replace(server.CONFIG, url="https://wb:18443", token="ttiowbk_x"),
    )
    captured = {}
    monkeypatch.setattr(
        server.CONN, "enable_service_autoconnect",
        lambda url, token, username=None: captured.update(url=url, token=token),
    )
    server._maybe_autoconnect()
    assert captured == {"url": "https://wb:18443", "token": "ttiowbk_x"}


# ---------------------------------------------------------------------------
# OAuth resource-server integration tests
# ---------------------------------------------------------------------------

def _oauth_cfg(monkeypatch):
    """Replace CONFIG with an OAuth-enabled variant and return it."""
    cfg = dataclasses.replace(
        server.CONFIG,
        transport="http",
        oauth_issuer="https://kc/realms/ttio",
        oauth_resource_url="http://127.0.0.1:8000/mcp",
        oauth_jwks_url="https://kc/realms/ttio/protocol/openid-connect/certs",
        oauth_token_url="https://kc/realms/ttio/protocol/openid-connect/token",
        oauth_client_id="ttio-mcp",
        oauth_client_secret="s3cr3t",
    )
    monkeypatch.setattr(server, "CONFIG", cfg)
    return cfg


def test_protected_resource_metadata_served(monkeypatch):
    """GET /.well-known/oauth-protected-resource/mcp -> 200 with RFC 9728 metadata."""
    _oauth_cfg(monkeypatch)
    app = server.build_app()
    client = TestClient(app.streamable_http_app())
    r = client.get("/.well-known/oauth-protected-resource/mcp")
    assert r.status_code == 200
    body = r.json()
    # resource must be (or end with) the resource URL path fragment
    assert body["resource"].endswith("/mcp")
    # authorization_servers must list the issuer
    assert "https://kc/realms/ttio" in body["authorization_servers"]


def test_mcp_requires_auth(monkeypatch):
    """POST /mcp with no Authorization -> 401 + WWW-Authenticate: Bearer."""
    _oauth_cfg(monkeypatch)
    app = server.build_app()
    client = TestClient(app.streamable_http_app(), raise_server_exceptions=False)
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401
    assert "Bearer" in r.headers.get("www-authenticate", "")


def test_non_oauth_app_has_no_metadata_route(monkeypatch):
    """With OAuth disabled, the protected-resource metadata route is not served."""
    cfg = dataclasses.replace(server.CONFIG, transport="http", oauth_issuer=None)
    monkeypatch.setattr(server, "CONFIG", cfg)
    app = server.build_app()
    client = TestClient(app.streamable_http_app())
    assert client.get("/.well-known/oauth-protected-resource/mcp").status_code == 404


def test_oauth_enabled_missing_field_raises(monkeypatch):
    """oauth_enabled but a required field unset -> build_app fails fast."""
    cfg = dataclasses.replace(
        server.CONFIG, transport="http",
        oauth_issuer="https://kc/realms/ttio", oauth_jwks_url=None,
    )
    monkeypatch.setattr(server, "CONFIG", cfg)
    with pytest.raises(ValueError):
        server.build_app()
