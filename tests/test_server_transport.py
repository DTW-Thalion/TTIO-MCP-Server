import dataclasses

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
