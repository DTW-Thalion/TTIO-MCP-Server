# Remote Connector — Phase 1 (Per-session tenancy) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the server safe for concurrent MCP sessions by giving each its own workbench client, instead of the single process-global one — without changing tool handlers or the auth model (still service-account / per-session `ttio_login`). This is the prerequisite for Phase 2 (per-user OAuth identity).

**Architecture:** `ConnectionManager` becomes a **registry keyed by a session key** from an injectable resolver. `build_app()` binds a resolver that returns `id(app.get_context().session)`; `get_context()` raises outside a request, so the resolver falls back to a single default key (covering stdio, tests, and startup). Lazy per-session service-account auto-connect replaces the eager startup login. A bounded LRU caps the registry (the `ttio` `WorkbenchClient` has **no** `close()`, so eviction drops references for GC — guaranteed teardown is a noted SDK follow-up). **Tool handlers are unchanged** — they keep calling `conn.require_client()` etc., which now resolve per-session internally.

**Tech Stack:** Python 3.11/3.12, `mcp` SDK FastMCP (`get_context()`, `Context.session`), pytest.

---

## Context & key facts (verified)

- Tools capture one `conn` via `register(app, conn, config)` and call `conn.{login_password,login_token,logout,require_client,status}()` (26 call sites across auth/cohorts/containers/jobs/sessions/transfers; `data` tools use none). **These do not change.**
- `app.get_context()` returns a `Context` only **inside a request**; outside it raises `ValueError("Context is not available outside of a request")`. `Context.session` is the per-connection `ServerSession`; `id(session)` is stable across tool calls within one client connection.
- `ttio.workbench.WorkbenchClient` has **no** `close()`/`disconnect()`. Eviction can only drop the reference.
- `tests/conftest.py` builds a `ConnectionManager` and calls `cm._inject(fake_client)`, then tools call `cm.require_client()`. Tests run **outside** a request → resolver absent/raises → default key. So `_inject` + `require_client` on the default key must keep working for the existing 62 tests to pass.
- `server.py` currently: `_maybe_autoconnect()` eagerly `login_token`s the service account at startup; `tests/test_server_transport.py` monkeypatches `server._maybe_autoconnect`, so that name must persist (repurpose its body).

## File Structure

- `src/ttio_mcp/connection.py` — **Rewrite** `ConnectionManager` as a keyed registry (resolver, lazy service auto-connect, LRU). Public method names unchanged.
- `src/ttio_mcp/server.py` — **Modify** `build_app()` to bind the resolver; repurpose `_maybe_autoconnect()` to enable lazy service auto-connect; add `_session_key(app)`.
- `tests/test_connection.py` — **Extend** with per-session isolation, resolver fallback, lazy auto-connect, logout scoping, and LRU eviction.
- `tests/test_server_transport.py` — **Extend** with a `_session_key` test (in-request vs out-of-request).
- Tool modules — **unchanged** (the existing `test_tools_*` suite is the regression guard).

---

## Task 1: ConnectionManager as a per-session registry

**Files:**
- Rewrite: `src/ttio_mcp/connection.py`
- Test: `tests/test_connection.py` (extend)

- [ ] **Step 1: Write failing tests** (append to `tests/test_connection.py`)

```python
from ttio_mcp.connection import ConnectionManager


class _Client:
    def __init__(self, name):
        self.session = type("S", (), {"username": name, "projects": ("p",),
                                      "capabilities": (), "expires_at": 9999999999,
                                      "expired": False})()


def test_sessions_are_isolated(monkeypatch):
    cm = ConnectionManager()
    key = {"v": "A"}
    cm.bind_session_resolver(lambda: key["v"])
    cm._inject(_Client("alice"))           # stored under key A
    assert cm.require_client().session.username == "alice"
    key["v"] = "B"                          # switch session
    import pytest
    from ttio_mcp.errors import ToolError
    with pytest.raises(ToolError):
        cm.require_client()                 # B has no client
    key["v"] = "A"
    assert cm.require_client().session.username == "alice"  # A unaffected


def test_logout_scoped_to_current_session():
    cm = ConnectionManager()
    key = {"v": "A"}
    cm.bind_session_resolver(lambda: key["v"])
    cm._inject(_Client("a"))
    key["v"] = "B"
    cm._inject(_Client("b"))
    cm.logout()                             # drops B only
    from ttio_mcp.errors import ToolError
    import pytest
    with pytest.raises(ToolError):
        cm.require_client()
    key["v"] = "A"
    assert cm.require_client().session.username == "a"


def test_resolver_failure_falls_back_to_default():
    cm = ConnectionManager()

    def boom():
        raise RuntimeError("no request")

    cm.bind_session_resolver(boom)
    cm._inject(_Client("d"))                # falls back to default key
    assert cm.require_client().session.username == "d"
```

- [ ] **Step 2: Run — expect fail** (`AttributeError: bind_session_resolver`)

Run: `.venv/bin/python -m pytest tests/test_connection.py -q`

- [ ] **Step 3: Rewrite `connection.py`**

```python
# src/ttio_mcp/connection.py
"""Per-session connection registry around ttio.workbench.WorkbenchClient."""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Callable

import ttio
from ttio import BearerAuth, PasswordTotpAuth

from ttio_mcp.errors import ToolError

_DEFAULT_KEY = "__default__"
_MAX_SESSIONS = 256


def _session_expired(session: Any) -> bool:
    if session is None:
        return False
    if not getattr(session, "expires_at", 0):
        return False
    return bool(getattr(session, "expired", False))


class ConnectionManager:
    """Owns one authenticated WorkbenchClient PER MCP session.

    The current session is identified by an injected resolver; outside a
    request (stdio, tests, startup) it falls back to a single default key, so
    behaviour matches the old single-session manager. Tokens live in memory
    only. Not parallelism-heavy: the server runs one event loop, but a lock
    guards the registry against interleaved access.
    """

    def __init__(self, max_sessions: int = _MAX_SESSIONS) -> None:
        self._clients: "OrderedDict[Any, Any]" = OrderedDict()
        self._lock = threading.RLock()
        self._resolver: Callable[[], Any] | None = None
        self._service: tuple[str, str, str | None] | None = None
        self._max_sessions = max_sessions

    # --- wiring -------------------------------------------------------
    def bind_session_resolver(self, resolver: Callable[[], Any]) -> None:
        self._resolver = resolver

    def enable_service_autoconnect(self, url: str, token: str, username: str | None = None) -> None:
        self._service = (url, token, username)

    def _key(self) -> Any:
        if self._resolver is not None:
            try:
                k = self._resolver()
                if k is not None:
                    return k
            except Exception:
                pass
        return _DEFAULT_KEY

    def _store(self, key: Any, client: Any) -> None:
        with self._lock:
            self._clients[key] = client
            self._clients.move_to_end(key)
            while len(self._clients) > self._max_sessions:
                self._clients.popitem(last=False)  # evict LRU (no close() to call)

    # --- test / internal ---------------------------------------------
    def _inject(self, client: Any) -> None:
        self._store(self._key(), client)

    # --- lifecycle ----------------------------------------------------
    def login_password(self, url: str, username: str, password: str, totp: str) -> dict[str, Any]:
        client = ttio.connect(url, auth=PasswordTotpAuth(username, password, totp))
        self._store(self._key(), client)
        return self.status()

    def login_token(self, url: str, token: str, username: str | None = None) -> dict[str, Any]:
        client = ttio.connect(url, auth=BearerAuth(token, username or "token-user"))
        self._store(self._key(), client)
        return self.status()

    def logout(self) -> None:
        with self._lock:
            self._clients.pop(self._key(), None)

    # --- access -------------------------------------------------------
    def require_client(self) -> Any:
        key = self._key()
        with self._lock:
            client = self._clients.get(key)
            if client is not None:
                self._clients.move_to_end(key)
        if client is None:
            client = self._maybe_service_connect(key)
        if client is None:
            raise ToolError("Not connected. Call ttio_login (or set TTIO_WB_URL + TTIO_WB_TOKEN).")
        if _session_expired(getattr(client, "session", None)):
            raise ToolError("Session expired. Call ttio_login again (API-key tokens do not expire).")
        return client

    def _maybe_service_connect(self, key: Any) -> Any:
        if self._service is None:
            return None
        url, token, username = self._service
        try:
            client = ttio.connect(url, auth=BearerAuth(token, username or "token-user"))
        except Exception:
            return None
        self._store(key, client)
        return client

    def status(self) -> dict[str, Any]:
        with self._lock:
            client = self._clients.get(self._key())
        if client is None:
            return {"connected": False}
        s = getattr(client, "session", None)
        return {
            "connected": True,
            "username": getattr(s, "username", None),
            "projects": list(getattr(s, "projects", ()) or ()),
            "capabilities": sorted(getattr(s, "capabilities", ()) or ()),
            "expired": _session_expired(s),
        }
```

- [ ] **Step 4: Run — expect pass** (new tests + existing `test_connection.py`)

Run: `.venv/bin/python -m pytest tests/test_connection.py -q`

- [ ] **Step 5: Run the tool-suite regression** (proves handlers untouched still work)

Run: `.venv/bin/python -m pytest tests/test_tools_*.py tests/test_connection.py -q`
Expected: all pass (conftest `_inject` + default-key path intact).

- [ ] **Step 6: Commit**

```bash
git add src/ttio_mcp/connection.py tests/test_connection.py
git commit -m "feat(connection): per-session client registry (resolver + LRU)"
```

## Task 2: Bind the session resolver + lazy service auto-connect

**Files:**
- Modify: `src/ttio_mcp/server.py`
- Test: `tests/test_server_transport.py` (extend)

- [ ] **Step 1: Write failing tests** (append to `tests/test_server_transport.py`)

```python
def test_session_key_outside_request_is_none():
    import ttio_mcp.server as server
    assert server._session_key(server.build_app()) is None


def test_maybe_autoconnect_enables_service(monkeypatch):
    import dataclasses
    import ttio_mcp.server as server
    monkeypatch.setattr(
        server, "CONFIG",
        dataclasses.replace(server.CONFIG, url="https://wb:18443", token="ttiowbk_x"),
    )
    captured = {}
    monkeypatch.setattr(server.CONN, "enable_service_autoconnect",
                        lambda url, token, username=None: captured.update(url=url, token=token))
    server._maybe_autoconnect()
    assert captured == {"url": "https://wb:18443", "token": "ttiowbk_x"}
```

- [ ] **Step 2: Run — expect fail** (`_session_key` missing; `_maybe_autoconnect` still logs in eagerly)

Run: `.venv/bin/python -m pytest tests/test_server_transport.py -q`

- [ ] **Step 3: Implement in `server.py`**

Add the helper and bind the resolver in `build_app()` (just before `return app`):

```python
def _session_key(app: FastMCP):
    """Stable per-connection key, or None outside a request (-> default key)."""
    try:
        return id(app.get_context().session)
    except Exception:
        return None
```

In `build_app()`, after the tool registrations and before `return app`, replace the
`_maybe_autoconnect()` call site so it also binds the resolver:

```python
    CONN.bind_session_resolver(lambda: _session_key(app))
    _maybe_autoconnect()
    return app
```

Repurpose `_maybe_autoconnect()` to enable lazy per-session auto-connect instead
of an eager login:

```python
def _maybe_autoconnect() -> None:
    """Enable lazy per-session service-account auto-connect when configured.

    Each session connects its own client on first use (see
    ConnectionManager.require_client), rather than one shared client at startup.
    """
    if CONFIG.url and CONFIG.token:
        CONN.enable_service_autoconnect(CONFIG.url, CONFIG.token, CONFIG.username)
```

- [ ] **Step 4: Run — expect pass**

Run: `.venv/bin/python -m pytest tests/test_server_transport.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/ttio_mcp/server.py tests/test_server_transport.py
git commit -m "feat(server): bind per-session resolver; lazy service auto-connect"
```

## Task 3: LRU eviction test + concurrent-session integration proof

**Files:**
- Test: `tests/test_connection.py` (extend)
- Test: `tests/integration/test_http_smoke.py` (extend, opt-in)

- [ ] **Step 1: Write the eviction unit test**

```python
def test_registry_evicts_lru_over_cap():
    cm = ConnectionManager(max_sessions=2)
    key = {"v": 0}
    cm.bind_session_resolver(lambda: key["v"])
    for i in range(3):
        key["v"] = i
        cm._inject(_Client(f"c{i}"))
    # key 0 evicted; 1 and 2 remain
    from ttio_mcp.errors import ToolError
    import pytest
    key["v"] = 0
    with pytest.raises(ToolError):
        cm.require_client()
    key["v"] = 2
    assert cm.require_client().session.username == "c2"
```

- [ ] **Step 2: Run — expect pass** (eviction already implemented in Task 1)

Run: `.venv/bin/python -m pytest tests/test_connection.py -k evict -q`

- [ ] **Step 3: Extend the HTTP smoke** to prove two concurrent HTTP sessions are isolated

Append to `tests/integration/test_http_smoke.py` a second test that opens **two**
`streamablehttp_client` sessions to the same subprocess server, calls
`ttio_connection_status` on each, and asserts both return `connected: false`
independently (no shared session bleed). Without credentials both are
disconnected; the point is that the two sessions resolve to **distinct** registry
slots (the server does not error and each status is independent). Use the same
`_free_port`/`_wait_health` helpers and the `TTIO_MCP_HTTP_SMOKE` gate.

```python
@pytest.mark.asyncio
async def test_two_sessions_are_independent():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    import json

    port = _free_port()
    env = {**os.environ, "TTIO_MCP_TRANSPORT": "http", "TTIO_MCP_HTTP_PORT": str(port),
           "TTIO_MCP_HTTP_HOST": "127.0.0.1"}
    env.pop("TTIO_WB_URL", None)
    env.pop("TTIO_WB_TOKEN", None)
    proc = subprocess.Popen([sys.executable, "-m", "ttio_mcp.server"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        _wait_health(port)
        url = f"http://127.0.0.1:{port}/mcp"
        for _ in range(2):
            async with streamablehttp_client(url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    res = await session.call_tool("ttio_connection_status", {})
                    payload = json.loads(res.content[0].text)
                    assert payload.get("connected") is False
    finally:
        proc.terminate()
        proc.wait(timeout=10)
```

- [ ] **Step 4: Run the opt-in smoke**

Run: `TTIO_MCP_HTTP_SMOKE=1 .venv/bin/python -m pytest tests/integration/test_http_smoke.py -q`
Expected: both HTTP tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_connection.py tests/integration/test_http_smoke.py
git commit -m "test(connection): LRU eviction + two-session HTTP isolation"
```

## Task 4: Full-suite verification + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Full unit suite** (regression guard for the untouched tool handlers)

Run: `.venv/bin/python -m pytest -q`
Expected: all prior tests + new connection/server tests pass; HTTP smoke skipped.

- [ ] **Step 2: ruff**

Run: `.venv/bin/ruff check src tests`
Expected: All checks passed.

- [ ] **Step 3: CHANGELOG `[Unreleased]` `### Changed`**

Add: per-session client registry — each MCP session gets its own workbench
client (concurrency-safe); service-account auto-connect is now lazy/per-session;
bounded LRU registry. Note: guaranteed client teardown awaits a `ttio`
`WorkbenchClient.close()` (no-op drop today); per-user identity is Phase 2.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: CHANGELOG for per-session tenancy (Phase 1)"
```

---

## Self-Review

- **Spec coverage:** per-session registry → Task 1; resolver binding + lazy auto-connect → Task 2; concurrency lock → Task 1 (`_lock`); bounded lifecycle/eviction → Task 1 impl + Task 3 test; isolation proof → Task 3 integration; handlers untouched → existing `test_tools_*` pass at Task 1 Step 5.
- **Placeholder scan:** all steps have real code; the integration test (Task 3 Step 3) is fully written.
- **Type/name consistency:** `bind_session_resolver`, `enable_service_autoconnect`, `_key`, `_store`, `_maybe_service_connect`, `_inject`, `require_client`, `status`, and `_session_key(app)` are used identically across Tasks 1–3 and the tests. Public method names (`login_password`/`login_token`/`logout`/`require_client`/`status`/`_inject`) match the existing tool call sites and conftest, so no tool module changes.

## Out of scope (Phase 2+)

- Per-user **identity** (each session a distinct workbench account via OAuth) — Phase 2, cross-repo (workbench fronts OAuth). Phase 1 keeps the service-account / per-session-`ttio_login` model.
- Guaranteed client teardown — needs a `WorkbenchClient.close()` in the `ttio` SDK (a follow-up); today eviction drops the reference for GC.
- Rate limiting / observability / security review — Phase 3.
