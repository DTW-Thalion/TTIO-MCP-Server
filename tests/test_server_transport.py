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
