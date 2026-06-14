# Remote Connector — Phase 0 (Streamable-HTTP transport) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a remote **streamable-HTTP** transport to `ttio-mcp` alongside the existing stdio transport, so one deployed instance can be added to Claude as a custom connector by URL — keeping the current single-session model and service-account auth (per-user OAuth/tenancy is Phases 1–2).

**Architecture:** `Config` gains a transport selector + HTTP host/port/path. `build_app()` constructs `FastMCP` with those HTTP settings and registers a `/healthz` route. `main()` dispatches: stdio uses the existing stdout-protected `_serve()`; `http` calls `FastMCP.run(transport="streamable-http")` (uvicorn + the streamable-HTTP session manager, built into the `mcp` SDK). A subprocess integration test proves an MCP client can reach the server over HTTP. A Dockerfile + deployment doc cover running it behind TLS.

**Tech Stack:** `mcp` SDK 1.27.2 (`FastMCP`, `streamablehttp_client`), uvicorn (transitive via `mcp`), Starlette responses, pytest.

---

## Context & key facts (verified against mcp 1.27.2)

- `FastMCP(name, **settings)` accepts `host`, `port`, `streamable_http_path` (default `/mcp`) as settings kwargs; defaults are `127.0.0.1:8000`.
- `app.run(transport="streamable-http")` serves via uvicorn using those settings and runs the streamable-HTTP session manager (handles its own lifespan).
- `app.custom_route(path, methods, name=None, include_in_schema=True)` registers an extra HTTP route on the app — used for `/healthz`.
- Client side: `from mcp.client.streamable_http import streamablehttp_client` → `async with streamablehttp_client(url) as (read, write, _): async with ClientSession(read, write) as s: await s.initialize()`.
- Current server (`src/ttio_mcp/server.py`): `CONN = ConnectionManager()` (process-global single session), `CONFIG = Config.from_env()`, `build_app()` registers the 7 tool modules + `_maybe_autoconnect()`, `_serve()` runs stdio with stdout protection, `main()` = `asyncio.run(_serve())`.
- **Phase-0 limitation (documented, not fixed here):** the single global `CONN` means every connected user shares ONE workbench session (the service account from `TTIO_WB_URL` + `TTIO_WB_TOKEN`). Per-session tenancy is Phase 1; per-user OAuth identity is Phase 2.
- Local dev: `.venv` at repo root; run tests with `.venv/bin/python -m pytest`.

## File Structure

- `src/ttio_mcp/config.py` — **Modify.** Add `transport`, `http_host`, `http_port`, `http_path` to `Config` + `from_env`.
- `src/ttio_mcp/server.py` — **Modify.** Pass HTTP settings to `FastMCP`; register `/healthz`; `main()` transport dispatch; add `_serve_http()`.
- `tests/test_config.py` — **Modify (or create).** Cover the new env vars + defaults.
- `tests/test_server_transport.py` — **Create.** Unit: `main()` dispatch; `/healthz` registered; FastMCP settings reflect config.
- `tests/integration/test_http_smoke.py` — **Create.** Subprocess HTTP server → `streamablehttp_client` → `initialize` + `list_tools` == 28.
- `Dockerfile` — **Create.** Container that runs the HTTP transport.
- `docs/remote-deployment.md` — **Create.** TLS, env, add-by-URL, the single-tenant caveat.
- `pyproject.toml` — **Modify.** `[project.optional-dependencies] http = ["uvicorn>=0.30"]` (belt-and-suspenders; `mcp` already pulls uvicorn) + CHANGELOG entry.
- `CHANGELOG.md` — **Modify.** `[Unreleased]` entry.

---

## Task 1: Config — transport selector + HTTP settings

**Files:**
- Modify: `src/ttio_mcp/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py  (add these; create the file if absent)
import importlib

from ttio_mcp.config import Config


def test_transport_defaults_to_stdio(monkeypatch):
    for k in ("TTIO_MCP_TRANSPORT", "TTIO_MCP_HTTP_HOST", "TTIO_MCP_HTTP_PORT", "TTIO_MCP_HTTP_PATH"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config.from_env()
    assert cfg.transport == "stdio"
    assert cfg.http_host == "127.0.0.1"
    assert cfg.http_port == 8000
    assert cfg.http_path == "/mcp"


def test_http_transport_from_env(monkeypatch):
    monkeypatch.setenv("TTIO_MCP_TRANSPORT", "http")
    monkeypatch.setenv("TTIO_MCP_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("TTIO_MCP_HTTP_PORT", "9001")
    monkeypatch.setenv("TTIO_MCP_HTTP_PATH", "/ttio")
    cfg = Config.from_env()
    assert (cfg.transport, cfg.http_host, cfg.http_port, cfg.http_path) == ("http", "0.0.0.0", 9001, "/ttio")


def test_transport_is_validated(monkeypatch):
    monkeypatch.setenv("TTIO_MCP_TRANSPORT", "carrier-pigeon")
    import pytest
    with pytest.raises(ValueError):
        Config.from_env()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: FAIL — `Config` has no `transport`/`http_*` attributes.

- [ ] **Step 3: Implement**

In `src/ttio_mcp/config.py`, add fields to the frozen dataclass and populate them in `from_env`:

```python
    transport: str
    http_host: str
    http_port: int
    http_path: str
```

and in `from_env`, before the `return cls(...)`:

```python
        transport = os.environ.get("TTIO_MCP_TRANSPORT", "stdio")
        if transport not in ("stdio", "http"):
            raise ValueError(f"TTIO_MCP_TRANSPORT must be 'stdio' or 'http', got {transport!r}")
        http_host = os.environ.get("TTIO_MCP_HTTP_HOST", "127.0.0.1")
        http_port = int(os.environ.get("TTIO_MCP_HTTP_PORT", "8000"))
        http_path = os.environ.get("TTIO_MCP_HTTP_PATH", "/mcp")
```

and pass them into the `cls(...)` call alongside the existing fields:

```python
            transport=transport,
            http_host=http_host,
            http_port=http_port,
            http_path=http_path,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ttio_mcp/config.py tests/test_config.py
git commit -m "feat(config): transport selector + HTTP host/port/path"
```

## Task 2: FastMCP HTTP settings + /healthz route

**Files:**
- Modify: `src/ttio_mcp/server.py`
- Test: `tests/test_server_transport.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server_transport.py
from starlette.testclient import TestClient

import ttio_mcp.server as server


def test_build_app_applies_http_settings(monkeypatch):
    from ttio_mcp.config import Config
    cfg = Config.from_env.__func__(Config)  # fresh default config
    monkeypatch.setattr(server, "CONFIG", cfg._replace_for_test() if hasattr(cfg, "_replace_for_test") else cfg)
    app = server.build_app()
    assert app.settings.streamable_http_path == server.CONFIG.http_path
    assert app.settings.port == server.CONFIG.http_port


def test_healthz_route_registered():
    app = server.build_app()
    client = TestClient(app.streamable_http_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

> Note: `dataclasses.replace` is the idiomatic way to vary a frozen `Config` in
> tests — the `_replace_for_test` guard above simply falls back to the default
> config, which is sufficient since this test only asserts the path/port are
> wired through. Prefer `dataclasses.replace(cfg, http_port=...)` if you vary it.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_server_transport.py -q`
Expected: FAIL — `build_app` does not set settings from config nor register `/healthz`.

- [ ] **Step 3: Implement**

In `src/ttio_mcp/server.py`, change `build_app()` to construct `FastMCP` with the config HTTP settings and register the health route:

```python
def build_app() -> FastMCP:
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
    # ... (leave the remaining six register() calls unchanged) ...
    _maybe_autoconnect()
    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_server_transport.py -q`
Expected: PASS (the health test plus the settings test).

- [ ] **Step 5: Commit**

```bash
git add src/ttio_mcp/server.py tests/test_server_transport.py
git commit -m "feat(server): FastMCP HTTP settings from config + /healthz route"
```

## Task 3: main() transport dispatch

**Files:**
- Modify: `src/ttio_mcp/server.py`
- Test: `tests/test_server_transport.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_server_transport.py
import dataclasses


def test_main_dispatches_http(monkeypatch):
    import ttio_mcp.server as server
    calls = {}
    monkeypatch.setattr(server, "CONFIG", dataclasses.replace(server.CONFIG, transport="http"))
    monkeypatch.setattr(server.FastMCP, "run", lambda self, transport=None, **kw: calls.setdefault("transport", transport))
    monkeypatch.setattr(server, "_maybe_autoconnect", lambda: None)
    server.main()
    assert calls["transport"] == "streamable-http"


def test_main_dispatches_stdio(monkeypatch):
    import ttio_mcp.server as server
    called = {}
    monkeypatch.setattr(server, "CONFIG", dataclasses.replace(server.CONFIG, transport="stdio"))
    monkeypatch.setattr(server, "_serve", lambda: called.setdefault("stdio", True))
    # _serve is async in prod; replace asyncio.run so we can use a sync stub
    monkeypatch.setattr(server.asyncio, "run", lambda coro: coro if not callable(coro) else called.setdefault("stdio", True))
    server.main()
    assert called.get("stdio") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_server_transport.py -k dispatch -q`
Expected: FAIL — `main()` always runs stdio.

- [ ] **Step 3: Implement**

In `src/ttio_mcp/server.py`, replace `main()` and add `_serve_http()`:

```python
def _serve_http() -> None:
    build_app().run(transport="streamable-http")


def main() -> None:
    if CONFIG.transport == "http":
        _serve_http()
    else:
        asyncio.run(_serve())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_server_transport.py -k dispatch -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ttio_mcp/server.py tests/test_server_transport.py
git commit -m "feat(server): main() dispatches stdio vs streamable-http"
```

## Task 4: HTTP integration smoke (subprocess + real client)

**Files:**
- Create: `tests/integration/test_http_smoke.py`

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_http_smoke.py
"""Opt-in HTTP transport smoke: start ttio-mcp over streamable-HTTP as a
subprocess and confirm a real MCP client can initialize and list all 28 tools.

Run with: TTIO_MCP_HTTP_SMOKE=1 .venv/bin/python -m pytest tests/integration/test_http_smoke.py
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TTIO_MCP_HTTP_SMOKE") != "1",
    reason="set TTIO_MCP_HTTP_SMOKE=1 to run the HTTP transport smoke",
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_health(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/healthz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("server did not become healthy")


@pytest.mark.asyncio
async def test_http_initialize_and_list_tools():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    port = _free_port()
    env = {**os.environ, "TTIO_MCP_TRANSPORT": "http", "TTIO_MCP_HTTP_PORT": str(port),
           "TTIO_MCP_HTTP_HOST": "127.0.0.1"}
    # No TTIO_WB_URL/TOKEN: auto-connect is a no-op; tools still register.
    env.pop("TTIO_WB_URL", None)
    env.pop("TTIO_WB_TOKEN", None)
    proc = subprocess.Popen([sys.executable, "-m", "ttio_mcp.server"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        _wait_health(port)
        async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                names = {t.name for t in result.tools}
        assert len(names) == 28
        assert "ttio_login" in names and "ttio_containers_list" in names
    finally:
        proc.terminate()
        proc.wait(timeout=10)
```

- [ ] **Step 2: Confirm `python -m ttio_mcp.server` is runnable**

`src/ttio_mcp/server.py` already ends with `if __name__ == "__main__": main()`. Verify:
Run: `.venv/bin/python -c "import ttio_mcp.server"` → no error.

- [ ] **Step 3: Run the smoke**

Run: `TTIO_MCP_HTTP_SMOKE=1 .venv/bin/python -m pytest tests/integration/test_http_smoke.py -q`
Expected: PASS — 28 tools listed over HTTP. (Skips cleanly without the env var.)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_http_smoke.py
git commit -m "test(http): opt-in streamable-HTTP subprocess smoke (28 tools)"
```

## Task 5: Dockerfile + deployment doc

**Files:**
- Create: `Dockerfile`
- Create: `docs/remote-deployment.md`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# Dockerfile — runs ttio-mcp over streamable-HTTP.
FROM python:3.12-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . /app
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir ".[http]"

FROM python:3.12-slim
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    TTIO_MCP_TRANSPORT=http \
    TTIO_MCP_HTTP_HOST=0.0.0.0 \
    TTIO_MCP_HTTP_PORT=8000
EXPOSE 8000
# Liveness via the /healthz route added in build_app().
HEALTHCHECK --interval=30s --timeout=3s CMD python -c \
    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"
CMD ["ttio-mcp"]
```

- [ ] **Step 2: Write `docs/remote-deployment.md`**

Cover, with exact commands: building/running the image; the required env
(`TTIO_WB_URL` + a service-account `TTIO_WB_TOKEN`; `TTIO_MCP_TRANSPORT=http`,
`TTIO_MCP_HTTP_HOST=0.0.0.0`, port, `TTIO_MCP_HTTP_PATH`); putting it behind a
TLS-terminating reverse proxy (the MCP endpoint must be HTTPS for Claude); the
public URL shape `https://host/mcp`; adding it to Claude as a **custom connector
by URL**; and a prominent **Phase-0 limitation** callout — the server holds ONE
shared workbench session (the service account), so all connector users act as
that identity; per-user identity arrives in Phase 2 (see
`docs/remote-connector-scope.md`). Note `/healthz` for load-balancer checks.

- [ ] **Step 3: Build the image (smoke)**

Run: `docker build -t ttio-mcp:phase0 .`
Expected: image builds. (Optional: `docker run --rm -p 8000:8000 ttio-mcp:phase0` then `curl localhost:8000/healthz` → `{"status":"ok"}`.)

- [ ] **Step 4: Commit**

```bash
git add Dockerfile docs/remote-deployment.md
git commit -m "feat(deploy): Dockerfile + remote deployment guide (streamable-HTTP)"
```

## Task 6: Packaging extra + CHANGELOG

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the `http` extra**

In `pyproject.toml` under `[project.optional-dependencies]`:

```toml
http = ["uvicorn>=0.30"]
```

(uvicorn is already pulled transitively by `mcp`'s HTTP transports; this pins it
explicitly for the deployment image and documents the dependency.)

- [ ] **Step 2: CHANGELOG entry**

Add an `[Unreleased]` `### Added` bullet: streamable-HTTP transport
(`TTIO_MCP_TRANSPORT=http`), `/healthz`, Dockerfile + remote-deployment guide;
note the Phase-0 single-shared-session limitation.

- [ ] **Step 3: Full unit suite**

Run: `.venv/bin/python -m pytest -q`
Expected: existing 55 + new config/transport tests pass; integration HTTP smoke skipped (no env var).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "build: http extra + CHANGELOG for streamable-HTTP transport"
```

---

## Self-Review

- **Spec coverage:** remote transport addable-by-URL → Tasks 2–5; keep stdio →
  Task 3 dispatch (stdio default unchanged); single-session/service-account kept
  → no `CONN` change, called out in Task 5 docs + CHANGELOG; provable end-to-end
  → Task 4 subprocess+client smoke (28 tools). Phases 1–2 (tenancy/OAuth)
  explicitly out of scope.
- **Placeholder scan:** all code steps contain real code; the deployment doc
  (Task 5 Step 2) is content-specified (env list, TLS, add-by-URL, limitation
  callout), not a "write docs" stub.
- **Type/name consistency:** `transport`/`http_host`/`http_port`/`http_path`
  are defined in Task 1 and used identically in Tasks 2–4; `build_app()`,
  `_serve_http()`, `main()`, `/healthz`, and `/mcp` (the default
  `streamable_http_path`) match across tasks and the Dockerfile healthcheck.

## Out of scope (later phases — see docs/remote-connector-scope.md)

- Per-session workbench-client registry (kill the global `CONN`) — **Phase 1**.
- OAuth resource-server + per-user identity (workbench fronts OAuth) — **Phase 2**.
- Rate limiting, observability, security review, directory submission — **Phase 3**.
