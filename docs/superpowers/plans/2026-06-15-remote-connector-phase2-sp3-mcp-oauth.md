# Phase 2 · SP3 — `ttio-mcp` OAuth resource server (Python) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ttio-mcp` an OAuth 2.1 resource server: validate the user's Keycloak access token (`aud=ttio-mcp`, JWKS signature, `iss`, scope), serve protected-resource metadata + `401`/`WWW-Authenticate` when unauthenticated, RFC 8693 token-exchange the user token for a `tti-workbench`-audience token at Keycloak, and build the Phase-1 per-session workbench client from the exchanged token.

**Architecture:** The `mcp` SDK (1.27.2) provides resource-server plumbing out of the box: `FastMCP(auth=AuthSettings(...), token_verifier=...)` auto-serves `/.well-known/oauth-protected-resource` (RFC 9728), enforces `required_scopes`, and returns `401` + `WWW-Authenticate` for missing/invalid tokens. SP3 supplies (a) a custom `KeycloakTokenVerifier` (PyJWT + `PyJWKClient`) that validates the JWT and returns an `AccessToken`, and (b) a token-exchange + per-session bridge: the existing `ConnectionManager` lazy-connect path, when OAuth is enabled and no client is cached for the session, reads the current validated `AccessToken`, exchanges its raw token for a `tti-workbench` token via httpx, and builds a `ttio.workbench.BearerAuth` client (cached per session, refreshed on expiry). When the `keycloak`/OAuth config is absent, none of this is wired and the server behaves exactly as today (stdio + `ttio_login`/headless bearer).

**Tech Stack:** Python 3.12, FastMCP / `mcp` 1.27.2 (`mcp.server.auth`: `AuthSettings`, `TokenVerifier`, `AccessToken`), PyJWT 2.13 (`PyJWKClient`, `jwt.decode`), `cryptography` 48, `httpx` 0.28, `ttio.workbench` (`connect`, `BearerAuth`). All deps already installed in the venv.

**Token contract (from the design spec):** issuer `https://<kc>/realms/ttio`; user access-token `aud=ttio-mcp`; exchange result `aud=tti-workbench`; `sub`=workbench account id, `preferred_username`=username; algs RS256/ES256; scope `ttio.connector`.

---

## Context & key facts (verified by exploration, 2026-06-15)

Repo: `\\wsl.localhost\Ubuntu\home\toddw\TTIO-MCP-Server` (Python). Build/test in WSL: `wsl -d Ubuntu -- bash -lc 'cd ~/TTIO-MCP-Server && .venv/bin/python -m pytest -q'`. Push via Windows git; `gh` on Windows. Branch off `origin/main` (`1418e40`).

- **Server** `src/ttio_mcp/server.py`: `build_app()` (lines 20–52) creates `FastMCP("ttio-mcp", host=…, port=…, streamable_http_path=…)`, registers `/healthz` via `app.custom_route(...)`, registers tool modules, then `CONN.bind_session_resolver(lambda: _session_key(app))` + `_maybe_autoconnect()`. `_session_key` = `id(app.get_context().session)`. `main()` dispatches `_serve_http()` (`build_app().run(transport="streamable-http")`) when `CONFIG.transport=="http"`, else stdio `_serve()`. Process singletons `CONN = ConnectionManager()`, `CONFIG = Config.from_env()` (lines 16–17).
- **Per-session** `src/ttio_mcp/connection.py`: `ConnectionManager` is an LRU `OrderedDict` keyed by `self._resolver()` (falls back to `"__default__"`). `require_client()` (lines 98–114): returns the cached client, else `_maybe_service_connect(key)`, else raises `ToolError("Not connected…")`; also raises if `_session_expired(client.session)`. `_maybe_service_connect` (116–125) builds `ttio.connect(url, auth=BearerAuth(token, username))` from the `_service` tuple set by `enable_service_autoconnect`. `login_token(url, token, username=None)` (88–91) = `ttio.connect(url, auth=BearerAuth(token, username or "token-user"))` + store. `_session_expired` treats `expires_at==0` as never-expires.
- **Config** `src/ttio_mcp/config.py`: frozen dataclass populated by `from_env()`; fields incl. `url` (`TTIO_WB_URL`), `token`, `username`, `transport`, `http_host/port/path`. Tests override via `dataclasses.replace(server.CONFIG, …)`.
- **mcp SDK auth** (`.venv/.../mcp/server/`):
  - `FastMCP.__init__(..., token_verifier: TokenVerifier | None = None, *, auth: AuthSettings | None = None)`. Set EXACTLY one of `token_verifier` / `auth_server_provider` when `auth` is given.
  - `AuthSettings(issuer_url: AnyHttpUrl, resource_server_url: AnyHttpUrl, required_scopes: list[str] | None, ...)`. When `resource_server_url` is set, `streamable_http_app()` auto-adds `create_protected_resource_routes(...)` and wraps the app with `RequireAuthMiddleware` (401 + `WWW-Authenticate: Bearer …, resource_metadata="…"`).
  - `TokenVerifier` protocol: `async def verify_token(self, token: str) -> AccessToken | None`.
  - `AccessToken(BaseModel)`: `token, client_id, scopes: list[str], expires_at: int|None, resource, subject, claims: dict|None`.
  - The validated token is available in the request/tool context via the auth contextvar — `from mcp.server.auth.middleware.auth_context import get_access_token` → `AccessToken | None` (VERIFY exact import path in Task 5; it is in `mcp/server/auth/middleware/auth_context.py`).
  - NO built-in JWT verifier — implement `verify_token` with PyJWT.
- **ttio.workbench** (`.venv/.../ttio/workbench/auth_providers.py`): `BearerAuth(token: str, username_: str, projects=(), capabilities=frozenset(), expires_at: int = 0)` synthesises a `Session` with no round-trip; `connect(url, *, auth)`.
- **Tests** `tests/`: unit mock pattern in `conftest.py` (`FakeWorkbenchClient`, `connected` fixture = `cm._inject(fake)`); tool tests call `app._tool_manager.get_tool(name).fn(...)`. `tests/test_server_transport.py` uses `TestClient(app.streamable_http_app())`. Integration `tests/integration/test_http_smoke.py` gated by `TTIO_MCP_HTTP_SMOKE=1`. `pyproject.toml`: `asyncio_mode="auto"`, `testpaths=["tests"]`; deps `mcp>=1.2`, `ttio[network,crypto] @ …@v1.7.1`; extras `dev` (pytest/ruff/mypy), `http` (uvicorn).
- **Installed**: PyJWT 2.13.0, cryptography 48.0.1, httpx 0.28.1 (transitive). Add `PyJWT` explicitly to deps (Task 1 / pyproject).

## File Structure

- New `src/ttio_mcp/oauth.py` — `KeycloakConfig` accessor helpers, `KeycloakTokenVerifier` (implements `TokenVerifier`), `exchange_for_workbench(...)` (httpx token-exchange). One responsibility: OAuth token validation + exchange. No FastMCP/connection imports (keep it unit-testable in isolation).
- Modify `src/ttio_mcp/config.py` — OAuth/Keycloak config fields + `from_env()`.
- Modify `src/ttio_mcp/server.py` — when OAuth enabled, construct `FastMCP(auth=…, token_verifier=…)`; pass the verifier/exchange into `CONN`.
- Modify `src/ttio_mcp/connection.py` — OAuth-aware lazy per-session connect (exchange the current access token → `BearerAuth` client).
- Modify `pyproject.toml` — add `PyJWT>=2.8` to deps (or an `oauth` extra folded into `http`).
- Tests: `tests/test_oauth.py` (verifier + exchange), extend `tests/test_server_transport.py` (metadata route + 401), `tests/test_connection.py` (per-session OAuth connect), new `tests/integration/test_http_oauth_smoke.py` (opt-in).

---

## Task 1: OAuth/Keycloak config

**Files:** Modify `src/ttio_mcp/config.py`; Modify `pyproject.toml`; Test `tests/test_config.py`.

- [ ] **Step 1: Write failing tests** (in `tests/test_config.py`, mirroring the existing env-var test style)

```python
def test_oauth_disabled_by_default(monkeypatch):
    for k in ("TTIO_MCP_OAUTH_ISSUER",):
        monkeypatch.delenv(k, raising=False)
    cfg = Config.from_env()
    assert cfg.oauth_enabled is False
    assert cfg.oauth_issuer is None

def test_oauth_enabled_from_env(monkeypatch):
    monkeypatch.setenv("TTIO_MCP_OAUTH_ISSUER", "https://kc.example/realms/ttio")
    monkeypatch.setenv("TTIO_MCP_OAUTH_RESOURCE_URL", "https://mcp.example/mcp")
    monkeypatch.setenv("TTIO_MCP_OAUTH_JWKS_URL", "https://kc.example/realms/ttio/protocol/openid-connect/certs")
    monkeypatch.setenv("TTIO_MCP_OAUTH_TOKEN_URL", "https://kc.example/realms/ttio/protocol/openid-connect/token")
    monkeypatch.setenv("TTIO_MCP_OAUTH_CLIENT_ID", "ttio-mcp")
    monkeypatch.setenv("TTIO_MCP_OAUTH_CLIENT_SECRET", "s3cr3t")
    cfg = Config.from_env()
    assert cfg.oauth_enabled is True
    assert cfg.oauth_audience == "ttio-mcp"            # default
    assert cfg.oauth_exchange_audience == "tti-workbench"  # default
    assert cfg.oauth_required_scopes == ["ttio.connector"]  # default
    assert cfg.oauth_client_id == "ttio-mcp"
```

- [ ] **Step 2: Run — expect FAIL.** `wsl -d Ubuntu -- bash -lc 'cd ~/TTIO-MCP-Server && .venv/bin/python -m pytest tests/test_config.py -q -k oauth'`

- [ ] **Step 3: Add fields to the `Config` dataclass** (after the existing fields)

```python
    oauth_issuer: str | None = None
    oauth_resource_url: str | None = None
    oauth_jwks_url: str | None = None
    oauth_token_url: str | None = None
    oauth_audience: str = "ttio-mcp"
    oauth_exchange_audience: str = "tti-workbench"
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_required_scopes: tuple[str, ...] = ("ttio.connector",)
    oauth_allowed_algs: tuple[str, ...] = ("RS256",)

    @property
    def oauth_enabled(self) -> bool:
        return bool(self.oauth_issuer)
```
> Match the dataclass's frozen/typing style. If the class disallows mutable defaults, tuples (used above) are fine; expose `oauth_required_scopes` as a list where the SDK wants a list (convert at the call site in Task 4). Adjust the test's `== ["ttio.connector"]` to `== ("ttio.connector",)` to match the tuple, or expose a list — keep test and impl consistent.

- [ ] **Step 4: Populate in `from_env()`**

```python
            oauth_issuer=os.environ.get("TTIO_MCP_OAUTH_ISSUER"),
            oauth_resource_url=os.environ.get("TTIO_MCP_OAUTH_RESOURCE_URL"),
            oauth_jwks_url=os.environ.get("TTIO_MCP_OAUTH_JWKS_URL"),
            oauth_token_url=os.environ.get("TTIO_MCP_OAUTH_TOKEN_URL"),
            oauth_audience=os.environ.get("TTIO_MCP_OAUTH_AUDIENCE", "ttio-mcp"),
            oauth_exchange_audience=os.environ.get("TTIO_MCP_OAUTH_EXCHANGE_AUDIENCE", "tti-workbench"),
            oauth_client_id=os.environ.get("TTIO_MCP_OAUTH_CLIENT_ID"),
            oauth_client_secret=os.environ.get("TTIO_MCP_OAUTH_CLIENT_SECRET"),
```
(Keep `oauth_required_scopes`/`oauth_allowed_algs` at their defaults unless an env override is desired; if added, parse comma-separated.)

- [ ] **Step 5: Add `PyJWT>=2.8` to `pyproject.toml` core `dependencies`.**

- [ ] **Step 6: Run — expect PASS.** Same pytest command. Also run the full `tests/test_config.py`.

- [ ] **Step 7: Commit** `feat(mcp): OAuth/Keycloak config fields`.

---

## Task 2: `KeycloakTokenVerifier`

**Files:** Create `src/ttio_mcp/oauth.py`; Test `tests/test_oauth.py`.

`KeycloakTokenVerifier.verify_token(token)` validates a Keycloak JWT (signature via JWKS, `aud=ttio-mcp`, `iss`, algs) and returns an `mcp` `AccessToken`, or `None` on any failure (never raises — the SDK treats `None` as 401).

- [ ] **Step 1: Write failing tests** (sign locally; point the verifier at an in-test JWKS via a fake `PyJWKClient`)

```python
import time, jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from ttio_mcp.oauth import KeycloakTokenVerifier

def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)

def _sign(priv, claims, kid="k1", alg="RS256"):
    return jwt.encode(claims, priv, algorithm=alg, headers={"kid": kid})

class _FakeJWKS:
    def __init__(self, priv, kid="k1"):
        self._pub = priv.public_key(); self._kid = kid
    def get_signing_key_from_jwt(self, token):
        class _K: pass
        k = _K(); k.key = self._pub; return k

def _verifier(priv):
    v = KeycloakTokenVerifier(
        jwks_url="https://kc/realms/ttio/protocol/openid-connect/certs",
        issuer="https://kc/realms/ttio", audience="ttio-mcp",
        algorithms=["RS256"], required_scopes=["ttio.connector"])
    v._jwks = _FakeJWKS(priv)   # inject fake JWKS client
    return v

async def test_valid_token_accepted():
    priv = _key(); now = int(time.time())
    tok = _sign(priv, {"iss":"https://kc/realms/ttio","aud":"ttio-mcp","sub":"01HACCT",
                       "preferred_username":"alice","scope":"ttio.connector","exp":now+300})
    at = await _verifier(priv).verify_token(tok)
    assert at is not None and at.subject == "01HACCT"
    assert "ttio.connector" in at.scopes and at.token == tok

async def test_wrong_audience_rejected():
    priv = _key(); now = int(time.time())
    tok = _sign(priv, {"iss":"https://kc/realms/ttio","aud":"tti-workbench","sub":"x","exp":now+300})
    assert await _verifier(priv).verify_token(tok) is None

async def test_expired_rejected():
    priv = _key(); now = int(time.time())
    tok = _sign(priv, {"iss":"https://kc/realms/ttio","aud":"ttio-mcp","sub":"x","exp":now-10})
    assert await _verifier(priv).verify_token(tok) is None

async def test_wrong_issuer_rejected():
    priv = _key(); now = int(time.time())
    tok = _sign(priv, {"iss":"https://evil/realms/x","aud":"ttio-mcp","sub":"x","exp":now+300})
    assert await _verifier(priv).verify_token(tok) is None

async def test_bad_signature_rejected():
    priv = _key(); other = _key(); now = int(time.time())
    tok = _sign(other, {"iss":"https://kc/realms/ttio","aud":"ttio-mcp","sub":"x","exp":now+300})
    assert await _verifier(priv).verify_token(tok) is None  # verifier holds priv's pub key
```

- [ ] **Step 2: Run — expect FAIL** (`ttio_mcp.oauth` missing).

- [ ] **Step 3: Implement `KeycloakTokenVerifier` in `src/ttio_mcp/oauth.py`**

```python
from __future__ import annotations
import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier


class KeycloakTokenVerifier(TokenVerifier):
    """Validate a Keycloak access token (aud=ttio-mcp) via JWKS. Returns an
    mcp AccessToken on success, None on any failure (-> 401)."""

    def __init__(self, *, jwks_url: str, issuer: str, audience: str,
                 algorithms: list[str], required_scopes: list[str] | None = None) -> None:
        self._jwks = PyJWKClient(jwks_url)   # fetches + caches JWKS
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms
        self._required_scopes = required_scopes or []

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, signing_key.key, algorithms=self._algorithms,
                audience=self._audience, issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except Exception:
            return None
        scopes = claims.get("scope", "")
        scope_list = scopes.split() if isinstance(scopes, str) else list(scopes or [])
        if any(s not in scope_list for s in self._required_scopes):
            return None
        return AccessToken(
            token=token,
            client_id=claims.get("azp") or claims.get("client_id") or self._audience,
            scopes=scope_list,
            expires_at=claims.get("exp"),
            subject=claims.get("sub"),
            claims=claims,
        )
```
> `PyJWKClient.get_signing_key_from_jwt` is sync; calling it inside an async method is fine (it's cached after the first fetch). If blocking-IO-in-async is a concern, wrap in `anyio.to_thread.run_sync` — but PyJWKClient caches, so the network hit is rare; keep it simple unless a reviewer flags it.

- [ ] **Step 4: Run — expect PASS** (all 5 tests).

- [ ] **Step 5: Commit** `feat(mcp): Keycloak JWT token verifier (JWKS, aud=ttio-mcp)`.

---

## Task 3: Token exchange (RFC 8693) client

**Files:** Modify `src/ttio_mcp/oauth.py`; Test `tests/test_oauth.py`.

`exchange_for_workbench(...)` POSTs an RFC 8693 token-exchange to Keycloak `/token` and returns the `tti-workbench`-audience access token + its expiry.

- [ ] **Step 1: Write failing test** (mock httpx)

```python
import httpx
from ttio_mcp.oauth import exchange_for_workbench

async def test_exchange_posts_and_returns_token(monkeypatch):
    captured = {}
    class _Resp:
        status_code = 200
        def json(self): return {"access_token": "wb.jwt.token", "expires_in": 300}
        def raise_for_status(self): pass
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, data=None, **k):
            captured["url"] = url; captured["data"] = data; return _Resp()
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    tok, exp = await exchange_for_workbench(
        user_token="user.jwt", token_url="https://kc/realms/ttio/protocol/openid-connect/token",
        client_id="ttio-mcp", client_secret="s3cr3t", audience="tti-workbench")
    assert tok == "wb.jwt.token" and exp > 0
    assert captured["data"]["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert captured["data"]["subject_token"] == "user.jwt"
    assert captured["data"]["audience"] == "tti-workbench"
    assert captured["data"]["subject_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** in `oauth.py`

```python
import time
import httpx

async def exchange_for_workbench(*, user_token: str, token_url: str, client_id: str,
                                 client_secret: str, audience: str,
                                 timeout: float = 10.0) -> tuple[str, int]:
    """RFC 8693 standard token exchange: swap the user's access token for a
    token whose aud is `audience` (tti-workbench). Returns (access_token, expires_at_epoch)."""
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": user_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "audience": audience,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(token_url, data=data)
        resp.raise_for_status()
        body = resp.json()
    expires_in = int(body.get("expires_in", 0))
    expires_at = int(time.time()) + expires_in if expires_in else 0
    return body["access_token"], expires_at
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit** `feat(mcp): RFC 8693 token exchange to tti-workbench audience`.

---

## Task 4: Wire the resource server into FastMCP

**Files:** Modify `src/ttio_mcp/server.py`; Test `tests/test_server_transport.py`.

When `CONFIG.oauth_enabled`, build the app with `auth=AuthSettings(...)` + the verifier; the SDK then serves protected-resource metadata and enforces auth on `/mcp`.

- [ ] **Step 1: Write failing tests** (use `TestClient(app.streamable_http_app())`)

```python
import dataclasses
from starlette.testclient import TestClient
import ttio_mcp.server as server

def _oauth_app(monkeypatch):
    cfg = dataclasses.replace(
        server.CONFIG, transport="http",
        oauth_issuer="https://kc/realms/ttio",
        oauth_resource_url="http://127.0.0.1:8000/mcp",
        oauth_jwks_url="https://kc/realms/ttio/protocol/openid-connect/certs",
        oauth_token_url="https://kc/realms/ttio/protocol/openid-connect/token",
        oauth_client_id="ttio-mcp", oauth_client_secret="s3cr3t")
    monkeypatch.setattr(server, "CONFIG", cfg)
    return server.build_app()

def test_protected_resource_metadata_served(monkeypatch):
    app = _oauth_app(monkeypatch)
    client = TestClient(app.streamable_http_app())
    r = client.get("/.well-known/oauth-protected-resource/mcp")
    assert r.status_code == 200
    body = r.json()
    assert body["resource"].endswith("/mcp")
    assert "https://kc/realms/ttio" in body["authorization_servers"]

def test_mcp_requires_auth(monkeypatch):
    app = _oauth_app(monkeypatch)
    client = TestClient(app.streamable_http_app())
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401
    assert "Bearer" in r.headers.get("www-authenticate", "")
```
> Verify the exact well-known path the SDK serves (`build_resource_metadata_url` inserts `/.well-known/oauth-protected-resource` before the path → `/.well-known/oauth-protected-resource/mcp`). Adjust the test path to whatever the installed SDK actually exposes (read `mcp/server/auth/routes.py`).

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement in `build_app()`** — branch on `CONFIG.oauth_enabled`

```python
def build_app() -> FastMCP:
    if CONFIG.oauth_enabled:
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
            "ttio-mcp", host=CONFIG.http_host, port=CONFIG.http_port,
            streamable_http_path=CONFIG.http_path,
            token_verifier=verifier,
            auth=AuthSettings(
                issuer_url=CONFIG.oauth_issuer,
                resource_server_url=CONFIG.oauth_resource_url,
                required_scopes=list(CONFIG.oauth_required_scopes),
            ),
        )
        CONN.enable_oauth(CONFIG)   # Task 5
    else:
        app = FastMCP("ttio-mcp", host=CONFIG.http_host, port=CONFIG.http_port,
                      streamable_http_path=CONFIG.http_path)
    # ... existing /healthz + tool registration + bind_session_resolver + _maybe_autoconnect ...
    return app
```
> Keep the `/healthz`, tool registration, `CONN.bind_session_resolver`, and `_maybe_autoconnect` exactly as before (run for both branches). `AuthSettings.issuer_url`/`resource_server_url` are `AnyHttpUrl` (pydantic) — passing `str` is accepted (pydantic coerces). If validation rejects a non-URL in tests, use real-looking `https://`/`http://` URLs (the tests above do).

- [ ] **Step 4: Run — expect PASS** + the full `tests/test_server_transport.py` (no regression to the non-OAuth path).

- [ ] **Step 5: Commit** `feat(mcp): serve OAuth protected-resource metadata + require auth on /mcp`.

---

## Task 5: Per-session token exchange → workbench client

**Files:** Modify `src/ttio_mcp/connection.py`; Test `tests/test_connection.py`.

When OAuth is enabled and no client is cached for the session, read the current validated `AccessToken`, exchange it, and build a per-session `BearerAuth` client.

- [ ] **Step 1: Write failing test** (inject a fake access token + stub the exchange)

```python
import asyncio, ttio_mcp.connection as connection
from ttio_mcp.connection import ConnectionManager

def test_oauth_session_connect(monkeypatch):
    cm = ConnectionManager()
    # OAuth config (only the fields the connect path needs)
    class _Cfg:
        url = "wss://wb/transport"
        oauth_token_url = "https://kc/token"; oauth_client_id = "ttio-mcp"
        oauth_client_secret = "s3cr3t"; oauth_exchange_audience = "tti-workbench"
    cm.enable_oauth(_Cfg())
    # current validated AccessToken for this request
    class _AT:
        token = "user.jwt"; subject = "01HACCT"; claims = {"preferred_username": "alice"}; expires_at = 0
    monkeypatch.setattr(connection, "_current_access_token", lambda: _AT())
    # stub the exchange + the workbench connect
    async def _fake_exchange(**kw): return ("wb.jwt", 0)
    monkeypatch.setattr(connection, "exchange_for_workbench", _fake_exchange)
    built = {}
    def _fake_connect(url, auth=None):
        built["url"] = url; built["token"] = auth.token; built["user"] = auth.username_
        class _C: session = None
        return _C()
    monkeypatch.setattr(connection.ttio, "connect", _fake_connect)
    client = cm.require_client()
    assert client is not None
    assert built["token"] == "wb.jwt" and built["user"] == "alice"

def test_oauth_no_access_token_raises(monkeypatch):
    cm = ConnectionManager()
    class _Cfg:
        url = "wss://wb/transport"; oauth_token_url = "https://kc/token"
        oauth_client_id = "ttio-mcp"; oauth_client_secret = "s"; oauth_exchange_audience = "tti-workbench"
    cm.enable_oauth(_Cfg())
    monkeypatch.setattr(connection, "_current_access_token", lambda: None)
    import pytest
    from ttio_mcp.errors import ToolError  # adjust import to the actual ToolError location
    with pytest.raises(ToolError):
        cm.require_client()
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement.** Add to `connection.py`:

```python
# Read the validated access token from the mcp auth contextvar. Isolated in a
# helper so tests can monkeypatch it. VERIFY the import path against the
# installed SDK (mcp/server/auth/middleware/auth_context.py).
def _current_access_token():
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token
        return get_access_token()
    except Exception:
        return None
```

Add OAuth state + an OAuth connect path to `ConnectionManager`:

```python
    def enable_oauth(self, config) -> None:
        self._oauth = config

    def _maybe_oauth_connect(self, key):
        if getattr(self, "_oauth", None) is None:
            return None
        at = _current_access_token()
        if at is None:
            return None
        import anyio
        cfg = self._oauth
        async def _do():
            return await exchange_for_workbench(
                user_token=at.token, token_url=cfg.oauth_token_url,
                client_id=cfg.oauth_client_id, client_secret=cfg.oauth_client_secret,
                audience=cfg.oauth_exchange_audience)
        wb_token, expires_at = anyio.from_thread.run(_do) if _in_worker_thread() else _run_sync(_do)
        username = (at.claims or {}).get("preferred_username") or at.subject or "oauth-user"
        client = ttio.connect(cfg.url, auth=BearerAuth(wb_token, username, expires_at=expires_at or 0))
        self._store(key, client)
        return client
```
> The exchange is async but `require_client()` is sync (called from sync tool fns under FastMCP's worker thread). Provide a small `_run_sync(coro)` that runs the coroutine to completion safely from a worker thread — e.g. `anyio.from_thread.run` when inside an anyio worker thread, else `asyncio.run`. KEEP THE EXACT MECHANISM SIMPLE and VERIFY under the real server in Task 6; the unit test monkeypatches `exchange_for_workbench` with a plain async stub, so wire `_run_sync` so a stub coroutine resolves. (If FastMCP tool fns can be async, prefer making the connect path async end-to-end — check whether the tool handlers are sync; the existing `require_client()` is sync, so a sync bridge is needed.)

Wire it into `require_client()` before the final raise:

```python
    if client is None:
        client = self._maybe_service_connect(key)
    if client is None:
        client = self._maybe_oauth_connect(key)   # NEW
    if client is None:
        raise ToolError("Not connected. ...")
```

- [ ] **Step 4: Run — expect PASS** (+ full `tests/test_connection.py`, no regression to existing session-isolation/LRU tests).

- [ ] **Step 5: Commit** `feat(mcp): per-session workbench client via token exchange`.

> **Design note / risk:** the sync↔async bridge for the exchange inside `require_client()` is the one spot needing live verification (Task 6). If it proves awkward, the alternative is to perform the exchange in a tiny Starlette middleware (after the SDK's auth middleware) that stashes the wb token in a contextvar, and have `_maybe_oauth_connect` read that — but the lazy-in-connection approach keeps all the per-session logic in one place and is preferred. Flag as DONE_WITH_CONCERNS if the bridge needs an approach change.

---

## Task 6: Opt-in live OAuth smoke + docs

**Files:** Create `tests/integration/test_http_oauth_smoke.py`; Modify `README`/`docs` (document the OAuth env vars); Modify `pyproject.toml` if a new extra is added.

- [ ] **Step 1: Document the OAuth env vars** (`TTIO_MCP_OAUTH_ISSUER`/`_RESOURCE_URL`/`_JWKS_URL`/`_TOKEN_URL`/`_CLIENT_ID`/`_CLIENT_SECRET`, defaults for audience/exchange-audience/scope) in the README/deployment doc, noting that setting `TTIO_MCP_OAUTH_ISSUER` turns the server into an OAuth resource server (HTTP transport).

- [ ] **Step 2: Write the opt-in integration smoke** (`tests/integration/test_http_oauth_smoke.py`, gated by `TTIO_MCP_OAUTH_SMOKE=1`) — mirrors `test_http_smoke.py`:
  - Requires a running Keycloak (SP1 realm) reachable via env (`TTIO_MCP_OAUTH_ISSUER` etc.) AND a way to obtain a user `ttio-mcp` token (env `TTIO_MCP_OAUTH_USER_TOKEN`, or skip).
  - Spawns `ttio-mcp` with `TTIO_MCP_TRANSPORT=http` + the OAuth env.
  - Asserts: `GET /.well-known/oauth-protected-resource/mcp` → 200; `POST /mcp` with no token → 401 + `WWW-Authenticate`; `POST /mcp` with the user token → the MCP session initialises (and, if a workbench is reachable, a read tool returns data).
  - `pytest.skip(...)` cleanly when the gate/env is absent (so CI unit runs are unaffected).

- [ ] **Step 3: Run unit suite** to confirm the new integration file imports cleanly and skips: `.venv/bin/python -m pytest -q`.

- [ ] **Step 4: (Manual, if a live Keycloak is available)** run `TTIO_MCP_OAUTH_SMOKE=1 … .venv/bin/python -m pytest tests/integration/test_http_oauth_smoke.py` and confirm green; otherwise note it as the documented manual verification step.

- [ ] **Step 5: Commit** `test(mcp): opt-in OAuth HTTP smoke + docs`.

---

## Self-Review

- **Spec coverage:** `/.well-known/oauth-protected-resource` + 401/`WWW-Authenticate` → Task 4 (SDK-served via `AuthSettings.resource_server_url`); validate user token via the SDK `TokenVerifier` (JWKS, `aud=ttio-mcp`) → Task 2; RFC 8693 token-exchange to `tti-workbench` → Task 3; per-session client from the exchanged token (Phase-1 registry, refresh) → Task 5 (reuses `BearerAuth`/`login_token` + `expires_at`); scope `ttio.connector` gate → Tasks 2+4 (`required_scopes`); unit tests for metadata/validation/exchange/per-session → Tasks 2/3/4/5; opt-in authenticated HTTP smoke → Task 6; config to enable it → Task 1.
- **Placeholder scan:** the verify-by-reading-SDK spots are explicit (the exact well-known path in Task 4; `get_access_token` import path in Task 5; the sync↔async bridge in Task 5) — each names the file to read and has a fallback. All app/verifier/exchange code is complete.
- **Type consistency:** `KeycloakTokenVerifier(jwks_url, issuer, audience, algorithms, required_scopes)` and `verify_token -> AccessToken|None` used identically in Tasks 2/4; `exchange_for_workbench(user_token, token_url, client_id, client_secret, audience) -> (str, int)` in Tasks 3/5; `Config.oauth_*` fields in Tasks 1/4/5; `ConnectionManager.enable_oauth(config)` + `_maybe_oauth_connect` in Tasks 4/5; `BearerAuth(token, username_, expires_at)` matches the installed `ttio.workbench` signature.

## Out of scope (later)
- The full three-component integration milestone (Claude → Keycloak → ttio-mcp → workbench round-trip) and Anthropic directory submission — a separate milestone after SP3.
- DCR/connector registration UX, refresh-token rotation handling beyond re-exchange on expiry, and production hosting/TLS — Phase 3.
