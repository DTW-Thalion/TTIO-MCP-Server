# TTIO Workbench MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy `.mpgo`-file MCP server with an MCP server that is a non-admin client of `tti-workbench-server`, exposing tio-browser's functionality (auth, browse, cohorts, jobs, sessions, transfers, data extraction) via the `ttio` Python SDK.

**Architecture:** A `FastMCP` app over a singleton `ConnectionManager` that holds one authenticated `ttio.workbench.WorkbenchClient` + `Session`. Domain tool modules delegate to the SDK; a `summarize`/`export` layer keeps results token-cheap with opt-in full-fidelity file export. All tool handlers are `async`; sync REST sub-client calls run via `asyncio.to_thread`.

**Tech Stack:** Python ≥3.11, `mcp` (FastMCP), `ttio[network,crypto,pqc] >= 1.7`, `numpy`, `pyarrow` (Parquet export), `pytest` + `pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-06-10-workbench-mcp-design.md`

**Conventions for every task:**
- Run tests from the repo root in WSL: `cd /home/toddw/TTIO-MCP-Server && .venv/bin/python -m pytest ...` (the venv has `ttio[network,crypto]` + dev extras installed by Task 1).
- Commit with explicit identity (WSL git): `git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m '...'`. Push is done separately from Windows git (see project memory); do **not** push in these tasks.
- `pytest.ini_options` already sets `asyncio_mode = "auto"`, so `async def test_*` runs without a decorator.

---

## Phase 0 — Teardown & dependency swap

### Task 1: Swap dependencies and tear down legacy modules

**Files:**
- Modify: `pyproject.toml`
- Delete: `src/ttio_mcp/catalog.py`, `src/ttio_mcp/hashes.py`, `src/ttio_mcp/keyring.py`, `src/ttio_mcp/db/` (whole dir), `src/ttio_mcp/uploader/` (whole dir), all of `src/ttio_mcp/tools/*.py` except a fresh `__init__.py`
- Delete: `alembic.ini`, `migrations/` (whole dir), `ttio_mcp.db`
- Delete legacy tests: every `tests/test_*.py`, `tests/_cloud.py`, `tests/_fixtures.py`, `tests/conftest.py`
- Keep: `tests/__init__.py`

- [ ] **Step 1: Rewrite `pyproject.toml` dependency + script sections**

Replace lines 7–8 (version/description), 24–45 (dependencies, optional-deps, scripts) with:

```toml
version = "0.9.0.dev0"
description = "MCP server exposing tti-workbench-server (non-admin) capabilities to LLM clients"
```

```toml
dependencies = [
    "mcp>=1.2",
    "ttio[network,crypto] @ git+https://github.com/DTW-Thalion/TTI-O.git@v1.7.1#subdirectory=python",
    "numpy>=1.24",
    "pyarrow>=16.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.5",
    "mypy>=1.10",
]
pqc = [
    "ttio[pqc] @ git+https://github.com/DTW-Thalion/TTI-O.git@v1.7.1#subdirectory=python",
]
cloud = [
    "ttio[cloud] @ git+https://github.com/DTW-Thalion/TTI-O.git@v1.7.1#subdirectory=python",
]

[project.scripts]
ttio-mcp = "ttio_mcp.server:main"
```

- [ ] **Step 2: Delete legacy source, migrations, db, and tests**

```bash
cd /home/toddw/TTIO-MCP-Server
rm -f src/ttio_mcp/catalog.py src/ttio_mcp/hashes.py src/ttio_mcp/keyring.py
rm -rf src/ttio_mcp/db src/ttio_mcp/uploader migrations
rm -f alembic.ini ttio_mcp.db
rm -f src/ttio_mcp/tools/*.py
rm -f tests/test_*.py tests/_cloud.py tests/_fixtures.py tests/conftest.py
printf '"""Workbench MCP tool modules."""\n' > src/ttio_mcp/tools/__init__.py
```

- [ ] **Step 3: Reset the package `__init__.py`**

Overwrite `src/ttio_mcp/__init__.py`:

```python
"""ttio-mcp: an MCP server client for tti-workbench-server."""
from __future__ import annotations

__version__ = "0.9.0.dev0"
```

- [ ] **Step 4: Recreate the venv with the new dependency set**

```bash
cd /home/toddw/TTIO-MCP-Server
rm -rf .venv && python3.12 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e ".[dev]"
```
Expected: install completes; `ttio`, `mcp`, `numpy`, `pyarrow` resolved. If the `ttio` git install fails, confirm network access to GitHub and that tag `v1.7.1` exists.

- [ ] **Step 5: Verify the SDK surface imports**

```bash
cd /home/toddw/TTIO-MCP-Server
.venv/bin/python -c "import ttio, mcp; from ttio import connect, WorkbenchClient, PasswordTotpAuth, BearerAuth, Session; from ttio.workbench.cohort import CohortQuery; from ttio.workbench.auth import InvalidCredentials, RateLimitExceeded; from ttio.workbench._http import WorkbenchHttpError; from mcp.server.fastmcp import FastMCP; print('ok')"
```
Expected: prints `ok`. If any import fails, fix the import path before continuing — later tasks depend on these exact symbols.

- [ ] **Step 6: Commit**

```bash
git add -A
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "chore: tear down .mpgo server, swap to ttio[network,crypto] SDK"
```

---

## Phase 1 — Foundation (config, errors, connection manager, server skeleton)

### Task 2: Config

**Files:**
- Create: `src/ttio_mcp/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from ttio_mcp.config import Config


def test_from_env_defaults(monkeypatch):
    for k in ("TTIO_WB_URL", "TTIO_WB_TOKEN", "TTIO_WB_USERNAME",
              "TTIO_MCP_EXPORT_DIR", "TTIO_MCP_CACHE_DIR", "TTIO_MCP_PAGE_SIZE"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config.from_env()
    assert cfg.url is None
    assert cfg.token is None
    assert cfg.page_size == 100
    assert cfg.export_dir.name == "exports"
    assert cfg.cache_dir.name == "cache"


def test_from_env_reads_values(monkeypatch, tmp_path):
    monkeypatch.setenv("TTIO_WB_URL", "wss://h:18443/transport")
    monkeypatch.setenv("TTIO_WB_TOKEN", "ttiowbk_abc")
    monkeypatch.setenv("TTIO_WB_USERNAME", "alice")
    monkeypatch.setenv("TTIO_MCP_EXPORT_DIR", str(tmp_path / "e"))
    monkeypatch.setenv("TTIO_MCP_CACHE_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("TTIO_MCP_PAGE_SIZE", "250")
    cfg = Config.from_env()
    assert cfg.url == "wss://h:18443/transport"
    assert cfg.token == "ttiowbk_abc"
    assert cfg.username == "alice"
    assert cfg.page_size == 250
    assert cfg.export_dir == tmp_path / "e"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ttio_mcp.config'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ttio_mcp/config.py
"""Runtime configuration for the workbench MCP server."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "ttio-mcp"


@dataclass(frozen=True)
class Config:
    """Server configuration, populated from environment variables.

    No secrets are persisted; ``token`` (an API key or bearer) is read
    from the environment only.
    """

    url: str | None
    token: str | None
    username: str | None
    export_dir: Path
    cache_dir: Path
    page_size: int

    @classmethod
    def from_env(cls) -> "Config":
        state = _default_state_dir()
        export_dir = Path(os.environ.get("TTIO_MCP_EXPORT_DIR", state / "exports"))
        cache_dir = Path(os.environ.get("TTIO_MCP_CACHE_DIR", state / "cache"))
        page_size = int(os.environ.get("TTIO_MCP_PAGE_SIZE", "100"))
        return cls(
            url=os.environ.get("TTIO_WB_URL") or None,
            token=os.environ.get("TTIO_WB_TOKEN") or None,
            username=os.environ.get("TTIO_WB_USERNAME") or None,
            export_dir=export_dir,
            cache_dir=cache_dir,
            page_size=page_size,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ttio_mcp/config.py tests/test_config.py
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "feat: config from environment"
```

### Task 3: Error mapping

**Files:**
- Create: `src/ttio_mcp/errors.py`
- Test: `tests/test_errors.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_errors.py
import pytest

from ttio_mcp.errors import ToolError, to_tool_error
from ttio.workbench.auth import InvalidCredentials, AccountDisabled, RateLimitExceeded
from ttio.workbench._http import WorkbenchHttpError


def test_invalid_credentials_maps():
    msg = to_tool_error(InvalidCredentials("bad"))
    assert "credential" in msg.lower()


def test_rate_limit_includes_retry_after():
    err = RateLimitExceeded("slow down")
    err.retry_after_seconds = 12
    msg = to_tool_error(err)
    assert "12" in msg


def test_http_403_names_capability():
    err = WorkbenchHttpError("forbidden", status=403, body={"error": "missing capability jobs.submit"})
    msg = to_tool_error(err)
    assert "403" in msg
    assert "jobs.submit" in msg


def test_account_disabled():
    assert "disabled" in to_tool_error(AccountDisabled("x")).lower()


def test_tool_error_is_exception():
    with pytest.raises(ToolError):
        raise ToolError("boom")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ttio_mcp.errors'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ttio_mcp/errors.py
"""Translate ttio/workbench exceptions into clean, actionable tool messages."""
from __future__ import annotations

from ttio.workbench.auth import (
    AccountDisabled,
    InvalidCredentials,
    RateLimitExceeded,
    WorkbenchAuthError,
)
from ttio.workbench._http import WorkbenchHttpError


class ToolError(Exception):
    """Raised by tools to signal a clean, user-facing failure."""


def to_tool_error(exc: Exception) -> str:
    """Return a single-line, user-facing message for *exc*."""
    if isinstance(exc, InvalidCredentials):
        return "Invalid credentials (401): username, password, or TOTP was rejected."
    if isinstance(exc, AccountDisabled):
        return "Account disabled (423): contact a workbench administrator."
    if isinstance(exc, RateLimitExceeded):
        retry = getattr(exc, "retry_after_seconds", None)
        tail = f" Retry after {retry}s." if retry else ""
        return f"Rate limited (429).{tail}"
    if isinstance(exc, WorkbenchHttpError):
        status = getattr(exc, "status", "?")
        body = getattr(exc, "body", None)
        detail = ""
        if isinstance(body, dict):
            detail = str(body.get("error") or body.get("reason") or "")
        if status == 403:
            return f"Forbidden (403): {detail or 'missing capability for this operation.'}"
        return f"Server error ({status}): {detail or exc}".rstrip()
    if isinstance(exc, WorkbenchAuthError):
        return f"Authentication error: {exc}"
    return f"Error: {exc}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_errors.py -v`
Expected: PASS (5 passed). If `RateLimitExceeded`/`WorkbenchHttpError` constructor signatures differ from the test, adjust the test to match the installed SDK (confirm via `.venv/bin/python -c "import inspect, ttio.workbench.auth as a; print(inspect.signature(a.RateLimitExceeded.__init__))"`).

- [ ] **Step 5: Commit**

```bash
git add src/ttio_mcp/errors.py tests/test_errors.py
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "feat: map ttio errors to tool messages"
```

### Task 4: ConnectionManager

**Files:**
- Create: `src/ttio_mcp/connection.py`
- Test: `tests/test_connection.py`
- Create: `tests/conftest.py` (shared fakes)

- [ ] **Step 1: Write shared fakes in `tests/conftest.py`**

```python
# tests/conftest.py
"""Shared test doubles for the workbench MCP server."""
from __future__ import annotations

import pytest

from ttio_mcp.connection import ConnectionManager


class FakeSession:
    def __init__(self, *, expired=False, username="alice", token="ttiowbs_x",
                 capabilities=(), projects=("adni",)):
        self.expired = expired
        self.username = username
        self.token = token
        self.user_id = "u1"
        self.capabilities = frozenset(capabilities)
        self.projects = tuple(projects)
        self.expires_at = 0 if expired else 9999999999
        self.provider = "password-totp"
        self.session_id = "s1"


class FakeWorkbenchClient:
    """Records calls and returns canned objects. Sub-client factories
    return objects supplied by the test via ``.set_subclient``."""

    def __init__(self, session=None):
        self.session = session or FakeSession()
        self._subclients: dict[str, object] = {}
        self.calls: list[tuple] = []

    def set_subclient(self, name, obj):
        self._subclients[name] = obj

    def containers(self):
        return self._subclients["containers"]

    def jobs(self):
        return self._subclients["jobs"]

    def pipelines(self):
        return self._subclients["pipelines"]

    def sessions(self):
        return self._subclients["sessions"]

    def federation(self):
        return self._subclients["federation"]

    def query(self, q):
        self.calls.append(("query", q))
        return self._subclients["query_result"]

    def preview_count(self, q):
        self.calls.append(("preview_count", q))
        return self._subclients.get("preview_count", 0)


@pytest.fixture
def fake_client():
    return FakeWorkbenchClient()


@pytest.fixture
def connected(fake_client):
    """A ConnectionManager pre-loaded with a fake client."""
    cm = ConnectionManager()
    cm._inject(fake_client)
    return cm, fake_client
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_connection.py
import pytest

from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import ToolError
from tests.conftest import FakeWorkbenchClient, FakeSession


def test_require_client_when_disconnected_raises():
    cm = ConnectionManager()
    with pytest.raises(ToolError) as ei:
        cm.require_client()
    assert "not connected" in str(ei.value).lower()


def test_inject_and_require():
    cm = ConnectionManager()
    fc = FakeWorkbenchClient()
    cm._inject(fc)
    assert cm.require_client() is fc


def test_expired_session_raises():
    cm = ConnectionManager()
    cm._inject(FakeWorkbenchClient(session=FakeSession(expired=True)))
    with pytest.raises(ToolError) as ei:
        cm.require_client()
    assert "expired" in str(ei.value).lower()


def test_status_disconnected():
    cm = ConnectionManager()
    st = cm.status()
    assert st["connected"] is False


def test_status_connected():
    cm = ConnectionManager()
    cm._inject(FakeWorkbenchClient())
    st = cm.status()
    assert st["connected"] is True
    assert st["username"] == "alice"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_connection.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'ttio_mcp.connection'`)

- [ ] **Step 4: Write minimal implementation**

```python
# src/ttio_mcp/connection.py
"""Single-session connection manager around ttio.workbench.WorkbenchClient."""
from __future__ import annotations

from typing import Any

import ttio
from ttio import BearerAuth, PasswordTotpAuth

from ttio_mcp.errors import ToolError


class ConnectionManager:
    """Owns at most one authenticated WorkbenchClient.

    Not threading-aware: the MCP server runs single-process, one event
    loop. Tokens live in memory only.
    """

    def __init__(self) -> None:
        self._client: Any | None = None

    # --- test / internal injection -------------------------------------
    def _inject(self, client: Any) -> None:
        self._client = client

    # --- lifecycle -----------------------------------------------------
    def login_password(self, url: str, username: str, password: str, totp: str) -> dict[str, Any]:
        self._client = ttio.connect(url, auth=PasswordTotpAuth(username, password, totp))
        return self.status()

    def login_token(self, url: str, token: str, username: str | None = None) -> dict[str, Any]:
        self._client = ttio.connect(url, auth=BearerAuth(token, username or "token-user"))
        return self.status()

    def logout(self) -> None:
        self._client = None

    # --- access --------------------------------------------------------
    def require_client(self) -> Any:
        if self._client is None:
            raise ToolError("Not connected. Call ttio_login (or set TTIO_WB_URL + TTIO_WB_TOKEN).")
        session = getattr(self._client, "session", None)
        if session is not None and getattr(session, "expired", False):
            raise ToolError("Session expired. Call ttio_login again (API-key tokens do not expire).")
        return self._client

    def status(self) -> dict[str, Any]:
        if self._client is None:
            return {"connected": False}
        s = getattr(self._client, "session", None)
        return {
            "connected": True,
            "username": getattr(s, "username", None),
            "projects": list(getattr(s, "projects", ()) or ()),
            "capabilities": sorted(getattr(s, "capabilities", ()) or ()),
            "expired": bool(getattr(s, "expired", False)),
        }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_connection.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add src/ttio_mcp/connection.py tests/test_connection.py tests/conftest.py
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "feat: single-session connection manager"
```

### Task 5: Server skeleton with FastMCP

**Files:**
- Create: `src/ttio_mcp/server.py` (overwrite)
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server.py
import asyncio

from ttio_mcp.server import build_app, CONN


def test_build_app_returns_fastmcp_and_registers_tools():
    app = build_app()
    names = asyncio.run(_tool_names(app))
    # auth tools must be registered by the skeleton wiring
    assert "ttio_connection_status" in names


async def _tool_names(app):
    tools = await app.list_tools()
    return {t.name for t in tools}


def test_conn_singleton_exists():
    assert CONN is not None
    assert CONN.status()["connected"] in (True, False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_server.py -v`
Expected: FAIL (`ImportError: cannot import name 'build_app'`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ttio_mcp/server.py
"""FastMCP entry point for ttio-mcp (tti-workbench-server client)."""
from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from ttio_mcp import __version__
from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager

# Process-wide singletons.
CONN = ConnectionManager()
CONFIG = Config.from_env()


def build_app() -> FastMCP:
    app = FastMCP("ttio-mcp", version=__version__)
    from ttio_mcp.tools import auth as auth_tools
    auth_tools.register(app, CONN, CONFIG)
    # Later phases append: containers, cohorts, jobs, sessions, transfers, data.
    _maybe_autoconnect()
    return app


def _maybe_autoconnect() -> None:
    """If a URL + token are configured, pre-connect with a bearer/API key."""
    if CONFIG.url and CONFIG.token:
        try:
            CONN.login_token(CONFIG.url, CONFIG.token, CONFIG.username)
        except Exception:
            # Leave disconnected; ttio_connection_status will report it.
            pass


def main() -> None:
    app = build_app()
    asyncio.run(app.run_stdio_async())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create the auth tool module stub so the import resolves**

```python
# src/ttio_mcp/tools/auth.py
"""Authentication / session tools."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager


def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:
    @app.tool()
    def ttio_connection_status() -> dict:
        """Report current workbench connection state (connected, user, projects, capabilities)."""
        return conn.status()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_server.py -v`
Expected: PASS (2 passed). If `FastMCP(...)` rejects the `version=` kwarg in the installed `mcp` version, drop it (`FastMCP("ttio-mcp")`) and re-run.

- [ ] **Step 6: Commit**

```bash
git add src/ttio_mcp/server.py src/ttio_mcp/tools/auth.py tests/test_server.py
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "feat: FastMCP server skeleton + connection_status tool"
```

---

## Phase 2 — Auth tools

### Task 6: login / whoami / logout

**Files:**
- Modify: `src/ttio_mcp/tools/auth.py`
- Test: `tests/test_tools_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_auth.py
from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.tools import auth as auth_tools
from tests.conftest import FakeWorkbenchClient


def _fn(app, name):
    # FastMCP stores the python callable on the registered Tool object.
    tool = app._tool_manager.get_tool(name)
    return tool.fn


def test_login_password_delegates(monkeypatch):
    cm = ConnectionManager()
    captured = {}

    def fake_login(url, username, password, totp):
        captured.update(url=url, username=username, password=password, totp=totp)
        cm._inject(FakeWorkbenchClient())
        return cm.status()

    monkeypatch.setattr(cm, "login_password", fake_login)
    app = FastMCP("t")
    auth_tools.register(app, cm, Config.from_env())
    out = _fn(app, "ttio_login")(url="wss://h:18443/transport",
                                 username="alice", password="pw", totp="123456")
    assert out["connected"] is True
    assert captured["username"] == "alice"


def test_whoami_requires_connection():
    cm = ConnectionManager()
    app = FastMCP("t")
    auth_tools.register(app, cm, Config.from_env())
    out = _fn(app, "ttio_whoami")()
    assert out["connected"] is False


def test_logout_clears():
    cm = ConnectionManager()
    cm._inject(FakeWorkbenchClient())
    app = FastMCP("t")
    auth_tools.register(app, cm, Config.from_env())
    out = _fn(app, "ttio_logout")()
    assert out["connected"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_auth.py -v`
Expected: FAIL (`ttio_login` not registered → `get_tool` raises)

- [ ] **Step 3: Write the implementation (overwrite `auth.py`)**

```python
# src/ttio_mcp/tools/auth.py
"""Authentication / session tools."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import to_tool_error


def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:
    @app.tool()
    def ttio_login(username: str, password: str, totp: str, url: str | None = None) -> dict:
        """Log in to the workbench server with username + password + current 6-digit TOTP.

        ``url`` defaults to the configured TTIO_WB_URL. Starts an in-memory
        session (expires ~24h). For headless use, set TTIO_WB_URL + TTIO_WB_TOKEN
        (an API key) instead and the server auto-connects at startup.
        """
        target = url or config.url
        if not target:
            return {"connected": False, "error": "No server URL: pass url= or set TTIO_WB_URL."}
        try:
            return conn.login_password(target, username, password, totp)
        except Exception as exc:  # noqa: BLE001 - surfaced as a tool message
            return {"connected": False, "error": to_tool_error(exc)}

    @app.tool()
    def ttio_whoami() -> dict:
        """Return the current session identity (username, projects, capabilities)."""
        return conn.status()

    @app.tool()
    def ttio_logout() -> dict:
        """Drop the in-memory session (client-side only; tokens are not persisted)."""
        conn.logout()
        return conn.status()

    @app.tool()
    def ttio_connection_status() -> dict:
        """Report current workbench connection state."""
        return conn.status()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tools_auth.py -v`
Expected: PASS (3 passed). If `app._tool_manager.get_tool(name).fn` differs in the installed `mcp` version, inspect with `.venv/bin/python -c "from mcp.server.fastmcp import FastMCP; a=FastMCP('t'); print(dir(a))"` and adjust the `_fn` helper (the public alternative is `await app.call_tool(name, args)`).

- [ ] **Step 5: Commit**

```bash
git add src/ttio_mcp/tools/auth.py tests/test_tools_auth.py
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "feat: auth tools (login/whoami/logout)"
```

---

## Phase 3 — Containers tools

### Task 7: list / get / layers / manifest

**Files:**
- Create: `src/ttio_mcp/tools/containers.py`
- Modify: `src/ttio_mcp/server.py` (register the module)
- Test: `tests/test_tools_containers.py`

SDK contract (verified): `client.containers()` → object with `.list(project, owner, limit, cursor) -> ContainerListPage` (fields `.containers`, `.next_cursor`, `.has_more`), `.get(uri) -> ContainerDetail`, `.layers(uri) -> list[ContainerLayer]`, `.manifest(uri) -> ContainerManifest`. Each returned object is a dataclass; serialize via `dataclasses.asdict` with a fallback to `vars`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_containers.py
import asyncio
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.tools import containers as ct
from tests.conftest import FakeWorkbenchClient


@dataclass
class _Page:
    containers: list
    next_cursor: str | None
    has_more: bool


@dataclass
class _C:
    uri: str
    project: str
    owner: str
    encrypted: bool


class _Containers:
    def list(self, project=None, owner=None, limit=None, cursor=None):
        return _Page(containers=[_C("uri:tio:1", "adni", "alice", False)],
                     next_cursor=None, has_more=False)

    def get(self, uri):
        return _C(uri, "adni", "alice", False)

    def layers(self, uri):
        return []

    def manifest(self, uri):
        return _C(uri, "adni", "alice", False)


def _app():
    cm = ConnectionManager()
    fc = FakeWorkbenchClient()
    fc.set_subclient("containers", _Containers())
    cm._inject(fc)
    app = FastMCP("t")
    ct.register(app, cm, Config.from_env())
    return app


def _call(app, name, **kw):
    tool = app._tool_manager.get_tool(name)
    res = tool.fn(**kw)
    return asyncio.run(res) if asyncio.iscoroutine(res) else res


def test_containers_list():
    out = _call(_app(), "ttio_containers_list")
    assert out["containers"][0]["uri"] == "uri:tio:1"
    assert out["has_more"] is False


def test_container_get():
    out = _call(_app(), "ttio_container_get", uri="uri:tio:1")
    assert out["uri"] == "uri:tio:1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_containers.py -v`
Expected: FAIL (`No module named 'ttio_mcp.tools.containers'`)

- [ ] **Step 3: Write the implementation**

```python
# src/ttio_mcp/tools/containers.py
"""Container browsing tools (read-only; no delete)."""
from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import to_tool_error


def _ser(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _ser(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_ser(x) for x in obj]
    return obj


def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:
    async def _run(fn, *a, **k):
        return await asyncio.to_thread(fn, *a, **k)

    @app.tool()
    async def ttio_containers_list(project: str | None = None, owner: str | None = None,
                                   limit: int | None = None, cursor: str | None = None) -> dict:
        """List server containers (paginated). Filters: project, owner. Use cursor to page."""
        try:
            cc = conn.require_client().containers()
            page = await _run(cc.list, project, owner, limit or config.page_size, cursor)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {
            "containers": [_ser(c) for c in page.containers],
            "next_cursor": getattr(page, "next_cursor", None),
            "has_more": bool(getattr(page, "has_more", False)),
        }

    @app.tool()
    async def ttio_container_get(uri: str) -> dict:
        """Get one container's detail row + file stats by URI."""
        try:
            return _ser(await _run(conn.require_client().containers().get, uri))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}

    @app.tool()
    async def ttio_container_layers(uri: str) -> dict:
        """List a container's auxiliary layers."""
        try:
            layers = await _run(conn.require_client().containers().layers, uri)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"layers": [_ser(x) for x in layers]}

    @app.tool()
    async def ttio_container_manifest(uri: str) -> dict:
        """Get a container's HDF5 manifest projection (runs, counts, ISA ids)."""
        try:
            return _ser(await _run(conn.require_client().containers().manifest, uri))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
```

- [ ] **Step 4: Register in `server.py`**

In `build_app()`, after the auth registration line, add:

```python
    from ttio_mcp.tools import containers as containers_tools
    containers_tools.register(app, CONN, CONFIG)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_containers.py tests/test_server.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/ttio_mcp/tools/containers.py src/ttio_mcp/server.py tests/test_tools_containers.py
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "feat: container browsing tools"
```

---

## Phase 4 — Cohort tools

### Task 8: predicate translator + query / preview_count

**Files:**
- Create: `src/ttio_mcp/tools/cohorts.py`
- Modify: `src/ttio_mcp/server.py`
- Test: `tests/test_tools_cohorts.py`

SDK contract (verified): `ttio.workbench.cohort` provides factory fns `container(field, op, value)`, `subject(...)`, `sample(...)`, `phenotype(name, op, value)`, predicate combinators via `&`/`|`/`~`, and `CohortQuery(select, predicate, order_by, limit, cursor)`. `client.query(CohortQuery) -> CohortResult` (iterable rows, `.next_cursor`, `len()`); `client.preview_count(CohortQuery) -> int`. The tool accepts the server's JSON predicate shape and translates it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_cohorts.py
import asyncio
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.tools import cohorts as co
from ttio_mcp.tools.cohorts import predicate_from_json
from tests.conftest import FakeWorkbenchClient


def test_predicate_from_json_leaf():
    p = predicate_from_json({"container_field": "owner", "op": "eq", "value": "alice"})
    assert p.to_json() == {"container_field": "owner", "op": "eq", "value": "alice"}


def test_predicate_from_json_composite():
    tree = {"op": "and", "children": [
        {"container_field": "owner", "op": "eq", "value": "alice"},
        {"subject_field": "sex", "op": "eq", "value": "F"},
    ]}
    p = predicate_from_json(tree)
    js = p.to_json()
    assert js["op"] == "and"
    assert len(js["children"]) == 2


def test_predicate_from_json_not():
    p = predicate_from_json({"op": "not", "child": {"container_field": "encrypted", "op": "eq", "value": True}})
    assert p.to_json()["op"] == "not"


@dataclass
class _Result:
    rows: list
    next_cursor: str | None
    def __iter__(self): return iter(self.rows)
    def __len__(self): return len(self.rows)


def _app(result=None, count=0):
    cm = ConnectionManager()
    fc = FakeWorkbenchClient()
    fc.set_subclient("query_result", result or _Result([{"uri": "uri:tio:1"}], None))
    fc.set_subclient("preview_count", count)
    cm._inject(fc)
    app = FastMCP("t")
    co.register(app, cm, Config.from_env())
    return app, fc


def _call(app, name, **kw):
    res = app._tool_manager.get_tool(name).fn(**kw)
    return asyncio.run(res) if asyncio.iscoroutine(res) else res


def test_cohort_query():
    app, fc = _app()
    out = _call(app, "ttio_cohort_query", select="containers",
                predicate={"container_field": "owner", "op": "eq", "value": "alice"})
    assert out["rows"][0]["uri"] == "uri:tio:1"
    assert fc.calls[0][0] == "query"


def test_cohort_preview_count():
    app, fc = _app(count=42)
    out = _call(app, "ttio_cohort_preview_count", select="subjects")
    assert out["count"] == 42
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_cohorts.py -v`
Expected: FAIL (`No module named 'ttio_mcp.tools.cohorts'`)

- [ ] **Step 3: Write the implementation**

```python
# src/ttio_mcp/tools/cohorts.py
"""Cohort query tools."""
from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from ttio.workbench import cohort as C

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import ToolError, to_tool_error

_LEAF_KEYS = {
    "container_field": C.container,
    "subject_field": C.subject,
    "sample_field": C.sample,
}


def predicate_from_json(node: dict[str, Any]) -> C.CohortPredicate:
    """Translate the server's JSON predicate shape into a CohortPredicate tree."""
    op = node.get("op")
    if op == "and":
        children = [predicate_from_json(c) for c in node["children"]]
        out = children[0]
        for c in children[1:]:
            out = out & c
        return out
    if op == "or":
        children = [predicate_from_json(c) for c in node["children"]]
        out = children[0]
        for c in children[1:]:
            out = out | c
        return out
    if op == "not":
        return ~predicate_from_json(node["child"])
    # leaf
    if "phenotype" in node:
        return C.phenotype(node["phenotype"], node.get("op", "eq"), node.get("value"))
    for key, factory in _LEAF_KEYS.items():
        if key in node:
            return factory(node[key], node.get("op", "eq"), node.get("value"))
    raise ToolError(f"Unrecognized predicate node: {sorted(node)}")


def _build_query(select: str, predicate: dict | None, order_by, limit: int, cursor: str | None):
    pred = predicate_from_json(predicate) if predicate else None
    return C.CohortQuery(select=select, predicate=pred,
                         order_by=tuple(order_by or ()), limit=limit, cursor=cursor)


def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:
    @app.tool()
    async def ttio_cohort_query(select: str = "containers", predicate: dict | None = None,
                                order_by: list | None = None, limit: int = 100,
                                cursor: str | None = None) -> dict:
        """Run a cohort query. select=containers|subjects|samples.

        predicate is a JSON tree: leaves use one of container_field / subject_field /
        sample_field / phenotype plus op (eq,ne,lt,gt,le,ge,in,like,exists) and value;
        composites use {"op":"and"|"or","children":[...]} or {"op":"not","child":...}.
        """
        try:
            q = _build_query(select, predicate, order_by, limit, cursor)
            result = await asyncio.to_thread(conn.require_client().query, q)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        rows = [dict(r) for r in result]
        return {"rows": rows, "count": len(rows), "next_cursor": getattr(result, "next_cursor", None)}

    @app.tool()
    async def ttio_cohort_preview_count(select: str = "containers", predicate: dict | None = None) -> dict:
        """Return the row count a cohort query would yield, without fetching rows."""
        try:
            q = _build_query(select, predicate, None, 100, None)
            n = await asyncio.to_thread(conn.require_client().preview_count, q)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"count": int(n), "select": select}
```

- [ ] **Step 4: Register in `server.py`**

```python
    from ttio_mcp.tools import cohorts as cohorts_tools
    cohorts_tools.register(app, CONN, CONFIG)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_cohorts.py -v`
Expected: PASS (6 passed). If a `CohortResult` row isn't `dict()`-able, replace `dict(r)` with `_ser(r)` reusing the serializer from `containers.py` (extract it to a small `tools/_serialize.py` and import in both).

- [ ] **Step 6: Commit**

```bash
git add src/ttio_mcp/tools/cohorts.py src/ttio_mcp/server.py tests/test_tools_cohorts.py
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "feat: cohort query tools + JSON predicate translator"
```

---

## Phase 5 — Jobs & pipelines tools

### Task 9: submit / list / get / cancel / events + pipelines list/get

**Files:**
- Create: `src/ttio_mcp/tools/jobs.py`
- Modify: `src/ttio_mcp/server.py`
- Test: `tests/test_tools_jobs.py`

SDK contract (verified): `client.jobs()` → `.submit(*, pipeline_id, inputs, params=None) -> Job`, `.list(*, status_filter=None, limit=None) -> list[Job]`, `.get(job_id) -> Job`, `.cancel(job_id) -> None`, `async events(job_id) -> AsyncIterator[JobEvent]`. `client.pipelines()` → `.list() -> list[Pipeline]`, `.get(pipeline_id) -> Pipeline`. (`.register(...)` exists but is admin — do NOT expose.) Cohort-query job inputs use `ttio.workbench.jobs.build_cohort_input(query_json)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_jobs.py
import asyncio
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.tools import jobs as jt
from tests.conftest import FakeWorkbenchClient


@dataclass
class _Job:
    job_id: str
    status: str


@dataclass
class _Evt:
    state: str
    data: dict


class _Jobs:
    def __init__(self):
        self.cancelled = None

    def submit(self, *, pipeline_id, inputs, params=None):
        return _Job("j1", "queued")

    def list(self, *, status_filter=None, limit=None):
        return [_Job("j1", "running")]

    def get(self, job_id):
        return _Job(job_id, "completed")

    def cancel(self, job_id):
        self.cancelled = job_id

    async def events(self, job_id):
        for s in ("queued", "running", "completed"):
            yield _Evt(s, {"job_id": job_id})


class _Pipes:
    def list(self):
        return [_Job("p1", "active")]

    def get(self, pipeline_id):
        return _Job(pipeline_id, "active")


def _app():
    cm = ConnectionManager()
    fc = FakeWorkbenchClient()
    fc.set_subclient("jobs", _Jobs())
    fc.set_subclient("pipelines", _Pipes())
    cm._inject(fc)
    app = FastMCP("t")
    jt.register(app, cm, Config.from_env())
    return app, fc


def _call(app, name, **kw):
    res = app._tool_manager.get_tool(name).fn(**kw)
    return asyncio.run(res) if asyncio.iscoroutine(res) else res


def test_job_submit():
    app, _ = _app()
    out = _call(app, "ttio_job_submit", pipeline_id="p1", inputs={"in": "uri:tio:1"})
    assert out["job_id"] == "j1"


def test_jobs_list():
    app, _ = _app()
    out = _call(app, "ttio_jobs_list")
    assert out["jobs"][0]["status"] == "running"


def test_job_cancel():
    app, fc = _app()
    out = _call(app, "ttio_job_cancel", job_id="j1")
    assert out["cancelled"] == "j1"
    assert fc.subclients_jobs_cancelled() == "j1" if hasattr(fc, "subclients_jobs_cancelled") else True


def test_job_events_collects():
    app, _ = _app()
    out = _call(app, "ttio_job_events", job_id="j1", max_events=2)
    assert len(out["events"]) == 2


def test_pipelines_list():
    app, _ = _app()
    out = _call(app, "ttio_pipelines_list")
    assert out["pipelines"][0]["job_id"] == "p1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_jobs.py -v`
Expected: FAIL (`No module named 'ttio_mcp.tools.jobs'`)

- [ ] **Step 3: Write the implementation**

```python
# src/ttio_mcp/tools/jobs.py
"""Jobs + pipelines tools (no pipeline registration — that is admin)."""
from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from mcp.server.fastmcp import FastMCP

from ttio.workbench.jobs import build_cohort_input

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import to_tool_error


def _ser(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _ser(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_ser(x) for x in obj]
    return obj


def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:
    async def _run(fn, *a, **k):
        return await asyncio.to_thread(fn, *a, **k)

    @app.tool()
    async def ttio_job_submit(pipeline_id: str, inputs: dict, params: dict | None = None) -> dict:
        """Submit a pipeline job. inputs maps slot->container_uri; a slot value of
        {"cohort_query": <query-json>} is auto-wrapped as a cohort input."""
        try:
            norm = {k: (build_cohort_input(v["cohort_query"]) if isinstance(v, dict) and "cohort_query" in v else v)
                    for k, v in inputs.items()}
            job = await _run(lambda: conn.require_client().jobs().submit(
                pipeline_id=pipeline_id, inputs=norm, params=params))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return _ser(job)

    @app.tool()
    async def ttio_jobs_list(status: str | None = None, limit: int | None = None) -> dict:
        """List jobs in the caller's project scope (optional status filter)."""
        try:
            jobs = await _run(lambda: conn.require_client().jobs().list(status_filter=status, limit=limit))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"jobs": [_ser(j) for j in jobs]}

    @app.tool()
    async def ttio_job_get(job_id: str) -> dict:
        """Get a single job row by id."""
        try:
            return _ser(await _run(conn.require_client().jobs().get, job_id))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}

    @app.tool()
    async def ttio_job_cancel(job_id: str) -> dict:
        """Cancel a job you own."""
        try:
            await _run(conn.require_client().jobs().cancel, job_id)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"cancelled": job_id}

    @app.tool()
    async def ttio_job_events(job_id: str, max_events: int = 20) -> dict:
        """Tail a job's live event stream (SSE); returns up to max_events then stops."""
        events: list[Any] = []
        try:
            client = conn.require_client()
            async for evt in client.jobs().events(job_id):
                events.append(_ser(evt))
                if len(events) >= max_events:
                    break
        except Exception as exc:  # noqa: BLE001
            return {"events": [_ser(e) for e in events], "error": to_tool_error(exc)}
        return {"events": events}

    @app.tool()
    async def ttio_pipelines_list() -> dict:
        """List pipelines visible to the caller's project scope."""
        try:
            ps = await _run(conn.require_client().pipelines().list)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"pipelines": [_ser(p) for p in ps]}

    @app.tool()
    async def ttio_pipeline_get(pipeline_id: str) -> dict:
        """Get a single pipeline definition by id."""
        try:
            return _ser(await _run(conn.require_client().pipelines().get, pipeline_id))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
```

- [ ] **Step 4: Register in `server.py`**

```python
    from ttio_mcp.tools import jobs as jobs_tools
    jobs_tools.register(app, CONN, CONFIG)
```

- [ ] **Step 5: Fix the cancel test assertion and run**

Edit `test_job_cancel` to simply assert `out["cancelled"] == "j1"` (drop the `hasattr` line — it was a stray). Then:

Run: `.venv/bin/python -m pytest tests/test_tools_jobs.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add src/ttio_mcp/tools/jobs.py src/ttio_mcp/server.py tests/test_tools_jobs.py
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "feat: jobs + pipelines tools"
```

---

## Phase 6 — Sessions tools

### Task 10: create / list / get / terminate / attach_url

**Files:**
- Create: `src/ttio_mcp/tools/sessions.py`
- Modify: `src/ttio_mcp/server.py`
- Test: `tests/test_tools_sessions.py`

SDK contract (verified): `client.sessions()` → `.create(*, project, engine_pin, image=None, command=None, env=None, bind_mounts=None, container_storage_root=None) -> Session`, `.list(*, status_filter=None, limit=None) -> list[Session]`, `.get(session_id) -> Session`, `.terminate(session_id) -> None`. `client.session_proxy(session_id, *, path="/") -> SessionProxyAttach` exposes the attach URL (do not embed a TTY).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_sessions.py
import asyncio
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.tools import sessions as st
from tests.conftest import FakeWorkbenchClient


@dataclass
class _Sess:
    session_id: str
    status: str


class _Sessions:
    def __init__(self):
        self.terminated = None

    def create(self, *, project, engine_pin, image=None, command=None,
               env=None, bind_mounts=None, container_storage_root=None):
        return _Sess("se1", "starting")

    def list(self, *, status_filter=None, limit=None):
        return [_Sess("se1", "running")]

    def get(self, session_id):
        return _Sess(session_id, "running")

    def terminate(self, session_id):
        self.terminated = session_id


class _Proxy:
    url = "wss://h:18443/v1/sessions/se1/connect"


def _app():
    cm = ConnectionManager()
    fc = FakeWorkbenchClient()
    sess = _Sessions()
    fc.set_subclient("sessions", sess)
    fc.session_proxy = lambda session_id, path="/": _Proxy()  # type: ignore[attr-defined]
    cm._inject(fc)
    app = FastMCP("t")
    st.register(app, cm, Config.from_env())
    return app, sess


def _call(app, name, **kw):
    res = app._tool_manager.get_tool(name).fn(**kw)
    return asyncio.run(res) if asyncio.iscoroutine(res) else res


def test_session_create():
    app, _ = _app()
    out = _call(app, "ttio_session_create", project="adni", engine_pin="shell")
    assert out["session_id"] == "se1"


def test_session_terminate():
    app, sess = _app()
    out = _call(app, "ttio_session_terminate", session_id="se1")
    assert out["terminated"] == "se1"
    assert sess.terminated == "se1"


def test_session_attach_url():
    app, _ = _app()
    out = _call(app, "ttio_session_attach_url", session_id="se1")
    assert out["attach_url"].endswith("/se1/connect")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_sessions.py -v`
Expected: FAIL (`No module named 'ttio_mcp.tools.sessions'`)

- [ ] **Step 3: Write the implementation**

```python
# src/ttio_mcp/tools/sessions.py
"""Interactive session tools. Attach is exposed as a URL only (no embedded TTY)."""
from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import to_tool_error


def _ser(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _ser(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_ser(x) for x in obj]
    return obj


def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:
    async def _run(fn, *a, **k):
        return await asyncio.to_thread(fn, *a, **k)

    @app.tool()
    async def ttio_session_create(project: str, engine_pin: str, image: str | None = None,
                                  command: list | None = None, env: dict | None = None,
                                  bind_mounts: dict | None = None) -> dict:
        """Start an interactive container session. engine_pin=shell|apptainer|podman|..."""
        try:
            sess = await _run(lambda: conn.require_client().sessions().create(
                project=project, engine_pin=engine_pin, image=image,
                command=command, env=env, bind_mounts=bind_mounts))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return _ser(sess)

    @app.tool()
    async def ttio_sessions_list(status: str | None = None, limit: int | None = None) -> dict:
        """List sessions in the caller's project scope."""
        try:
            ss = await _run(lambda: conn.require_client().sessions().list(status_filter=status, limit=limit))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"sessions": [_ser(s) for s in ss]}

    @app.tool()
    async def ttio_session_get(session_id: str) -> dict:
        """Get a single session row by id."""
        try:
            return _ser(await _run(conn.require_client().sessions().get, session_id))
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}

    @app.tool()
    async def ttio_session_terminate(session_id: str) -> dict:
        """Terminate a session you own."""
        try:
            await _run(conn.require_client().sessions().terminate, session_id)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"terminated": session_id}

    @app.tool()
    async def ttio_session_attach_url(session_id: str, path: str = "/") -> dict:
        """Return the WS attach URL for a running session (connect with your own client)."""
        try:
            proxy = conn.require_client().session_proxy(session_id, path=path)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        url = getattr(proxy, "url", None) or getattr(proxy, "attach_url", None)
        return {"attach_url": url, "session_id": session_id}
```

- [ ] **Step 4: Register in `server.py`**

```python
    from ttio_mcp.tools import sessions as sessions_tools
    sessions_tools.register(app, CONN, CONFIG)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_sessions.py -v`
Expected: PASS (3 passed). If `session_proxy` returns an object whose URL attribute is neither `url` nor `attach_url`, inspect it (`.venv/bin/python -c "import inspect,ttio.workbench.session_proxy as m; print([n for n in dir(m.SessionProxyAttach) if not n.startswith('__')])"`) and extend the fallback.

- [ ] **Step 6: Commit**

```bash
git add src/ttio_mcp/tools/sessions.py src/ttio_mcp/server.py tests/test_tools_sessions.py
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "feat: interactive session tools (attach as URL)"
```

---

## Phase 7 — Transfer tools

### Task 11: upload / download with mode (plain | byok | server-kek | pqc) + federation

**Files:**
- Create: `src/ttio_mcp/tools/transfers.py`
- Modify: `src/ttio_mcp/server.py`
- Test: `tests/test_tools_transfers.py`

SDK contract (verified from the exploration report): async client methods —
`upload_path(*, project, container_uri, path, resume=None, progress=None, chunk_size=None)`,
`download_bytes(*, container_uri, filters=None, output_mode="binary", max_au=0)`,
`upload_encrypted(*, project, container_uri, tio_path, key, encrypt_headers=False, resume=None)`,
`download_decrypted(*, container_uri, key, out_tio_path, filters=None, max_au=0)`,
`upload_encrypted_multi(*, project, container_uri, tio_path, recipients, server_kek_id=None, encrypt_headers=False, resume=None, preview=False)` with `ServerRecipient(recipient_id, kek_id, algorithm="aes-256-gcm")`,
`download_via_server(*, container_uri, out_tio_path, filters=None, max_au=0) -> dict`,
`upload_encrypted_pqc(*, project, container_uri, tio_path, recipient_public_key, preview=False, ...)`,
`download_decrypted_pqc(*, container_uri, recipient_private_key, out_tio_path, preview=False, ...)`.
`client.federation().peers() -> list[Peer]`. Keys are 32 raw bytes; the tool accepts hex/base64 strings and decodes them.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_transfers.py
import asyncio
import base64

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.tools import transfers as tr
from tests.conftest import FakeWorkbenchClient


class _Client(FakeWorkbenchClient):
    def __init__(self):
        super().__init__()
        self.recorded = {}

    async def upload_path(self, *, project, container_uri, path, resume=None, progress=None, chunk_size=None):
        self.recorded = dict(mode="plain", project=project, uri=container_uri, path=path)
        return type("R", (), {"container_uri": container_uri, "last_acked_au_sequence": 3, "resume_handle": None})()

    async def upload_encrypted_multi(self, *, project, container_uri, tio_path, recipients,
                                     server_kek_id=None, encrypt_headers=False, resume=None, preview=False):
        self.recorded = dict(mode="server-kek", kek=recipients[0].kek_id, uri=container_uri)
        return type("R", (), {"container_uri": container_uri, "last_acked_au_sequence": 3, "resume_handle": None})()

    async def download_via_server(self, *, container_uri, out_tio_path, filters=None, max_au=0):
        self.recorded = dict(mode="server-kek-dl", uri=container_uri, out=out_tio_path)
        return {"run_0001": {"mz": [1, 2, 3]}}


def _app():
    cm = ConnectionManager()
    fc = _Client()
    fc.set_subclient("federation", type("F", (), {"peers": lambda self: []})())
    cm._inject(fc)
    app = FastMCP("t")
    tr.register(app, cm, Config.from_env())
    return app, fc


def _call(app, name, **kw):
    res = app._tool_manager.get_tool(name).fn(**kw)
    return asyncio.run(res) if asyncio.iscoroutine(res) else res


def test_upload_plain(tmp_path):
    f = tmp_path / "x.tio"
    f.write_bytes(b"data")
    app, fc = _app()
    out = _call(app, "ttio_upload", mode="plain", project="adni",
                container_uri="uri:tio:1", path=str(f))
    assert fc.recorded["mode"] == "plain"
    assert out["last_acked_au_sequence"] == 3


def test_upload_server_kek(tmp_path):
    f = tmp_path / "x.tio"
    f.write_bytes(b"data")
    app, fc = _app()
    out = _call(app, "ttio_upload", mode="server-kek", project="adni",
                container_uri="uri:tio:1", path=str(f), kek_id="server:rewrap-v1")
    assert fc.recorded["mode"] == "server-kek"
    assert fc.recorded["kek"] == "server:rewrap-v1"


def test_download_server_kek(tmp_path):
    out_path = tmp_path / "out.tio"
    app, fc = _app()
    out = _call(app, "ttio_download", mode="server-kek",
                container_uri="uri:tio:1", out_path=str(out_path))
    assert fc.recorded["mode"] == "server-kek-dl"
    assert out["out_path"] == str(out_path)


def test_federation_peers():
    app, _ = _app()
    out = _call(app, "ttio_federation_peers")
    assert out["peers"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_transfers.py -v`
Expected: FAIL (`No module named 'ttio_mcp.tools.transfers'`)

- [ ] **Step 3: Write the implementation**

```python
# src/ttio_mcp/tools/transfers.py
"""Upload/download tools with a single mode selector; plus federation peers."""
from __future__ import annotations

import base64
import dataclasses
from typing import Any

from mcp.server.fastmcp import FastMCP

from ttio.workbench.client import ServerRecipient

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import ToolError, to_tool_error

_MODES = {"plain", "byok", "server-kek", "pqc"}


def _decode_key(s: str) -> bytes:
    """Accept hex (64 chars) or base64 for a 32-byte key."""
    if len(s) == 64:
        try:
            return bytes.fromhex(s)
        except ValueError:
            pass
    return base64.b64decode(s)


def _ser(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _ser(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_ser(x) for x in obj]
    return obj


def _result(res: Any) -> dict:
    return {
        "container_uri": getattr(res, "container_uri", None),
        "last_acked_au_sequence": getattr(res, "last_acked_au_sequence", None),
        "resume_handle": getattr(res, "resume_handle", None),
    }


def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:
    @app.tool()
    async def ttio_upload(project: str, container_uri: str, path: str, mode: str = "plain",
                          key: str | None = None, kek_id: str | None = None,
                          recipient_public_key: str | None = None,
                          encrypt_headers: bool = False, preview: bool = False) -> dict:
        """Upload a local .tio to the server.

        mode=plain         : no encryption.
        mode=byok          : caller key (hex/base64, 32 bytes), AES-256-GCM per-AU.
        mode=server-kek    : multi-recipient with a server ServerRecipient(kek_id) (HSM-wrapped).
        mode=pqc           : ML-KEM-1024 recipient_public_key (preview-gated; pass preview=true).
        """
        if mode not in _MODES:
            return {"error": f"mode must be one of {sorted(_MODES)}"}
        try:
            client = conn.require_client()
            if mode == "plain":
                res = await client.upload_path(project=project, container_uri=container_uri, path=path)
            elif mode == "byok":
                if not key:
                    raise ToolError("byok upload requires key=")
                res = await client.upload_encrypted(project=project, container_uri=container_uri,
                                                    tio_path=path, key=_decode_key(key),
                                                    encrypt_headers=encrypt_headers)
            elif mode == "server-kek":
                if not kek_id:
                    raise ToolError("server-kek upload requires kek_id=")
                rec = ServerRecipient(recipient_id="", kek_id=kek_id)
                res = await client.upload_encrypted_multi(project=project, container_uri=container_uri,
                                                          tio_path=path, recipients=[rec],
                                                          encrypt_headers=encrypt_headers)
            else:  # pqc
                if not recipient_public_key:
                    raise ToolError("pqc upload requires recipient_public_key=")
                res = await client.upload_encrypted_pqc(project=project, container_uri=container_uri,
                                                        tio_path=path,
                                                        recipient_public_key=_decode_key(recipient_public_key),
                                                        preview=preview)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return _result(res)

    @app.tool()
    async def ttio_download(container_uri: str, out_path: str, mode: str = "plain",
                            key: str | None = None, recipient_private_key: str | None = None,
                            filters: dict | None = None, max_au: int = 0, preview: bool = False) -> dict:
        """Download a container to a local file.

        mode=plain      : write raw .tis bytes to out_path.
        mode=byok       : caller key (hex/base64) decrypts per-AU; out_path is a plaintext .tio.
        mode=server-kek : download_via_server — server unwraps the DEK via HSM; out_path left decrypted.
        mode=pqc        : ML-KEM-1024 recipient_private_key decrypts (preview-gated).
        filters: selective-access dict (ms_level, polarity, retention_time_min/max,
                 precursor_mz_min/max, precursor_charge).
        """
        if mode not in _MODES:
            return {"error": f"mode must be one of {sorted(_MODES)}"}
        try:
            client = conn.require_client()
            if mode == "plain":
                res = await client.download_bytes(container_uri=container_uri, filters=filters,
                                                  output_mode="binary", max_au=max_au)
                with open(out_path, "wb") as fh:
                    fh.write(getattr(res, "payload", b"") or b"")
                return {"out_path": out_path, "bytes": len(getattr(res, "payload", b"") or b"")}
            if mode == "byok":
                if not key:
                    raise ToolError("byok download requires key=")
                await client.download_decrypted(container_uri=container_uri, key=_decode_key(key),
                                                out_tio_path=out_path, filters=filters, max_au=max_au)
                return {"out_path": out_path}
            if mode == "server-kek":
                meta = await client.download_via_server(container_uri=container_uri,
                                                        out_tio_path=out_path, filters=filters, max_au=max_au)
                return {"out_path": out_path, "runs": sorted(meta.keys()) if isinstance(meta, dict) else None}
            # pqc
            if not recipient_private_key:
                raise ToolError("pqc download requires recipient_private_key=")
            await client.download_decrypted_pqc(container_uri=container_uri,
                                                recipient_private_key=_decode_key(recipient_private_key),
                                                out_tio_path=out_path, preview=preview)
            return {"out_path": out_path}
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}

    @app.tool()
    async def ttio_federation_peers() -> dict:
        """List federation peers (empty on single-node v1.0)."""
        try:
            peers = conn.require_client().federation().peers()
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"peers": [_ser(p) for p in peers]}
```

- [ ] **Step 4: Register in `server.py`**

```python
    from ttio_mcp.tools import transfers as transfers_tools
    transfers_tools.register(app, CONN, CONFIG)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_transfers.py -v`
Expected: PASS (4 passed). If `ServerRecipient` import path differs, confirm with `.venv/bin/python -c "from ttio.workbench.client import ServerRecipient; print('ok')"` and fix the import.

- [ ] **Step 6: Commit**

```bash
git add src/ttio_mcp/tools/transfers.py src/ttio_mcp/server.py tests/test_tools_transfers.py
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "feat: transfer tools (plain/byok/server-kek/pqc) + federation peers"
```

---

## Phase 8 — Summarize & export utilities

### Task 12: summarize

**Files:**
- Create: `src/ttio_mcp/summarize.py`
- Test: `tests/test_summarize.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_summarize.py
import numpy as np

from ttio_mcp.summarize import array_summary, top_peaks, downsample


def test_array_summary():
    s = array_summary(np.array([1.0, 2.0, 3.0, 4.0]))
    assert s["count"] == 4
    assert s["min"] == 1.0
    assert s["max"] == 4.0
    assert abs(s["mean"] - 2.5) < 1e-9


def test_top_peaks():
    mz = np.array([100.0, 200.0, 300.0])
    inten = np.array([5.0, 50.0, 25.0])
    peaks = top_peaks(mz, inten, n=2)
    assert peaks[0] == {"mz": 200.0, "intensity": 50.0}
    assert peaks[1]["mz"] == 300.0


def test_downsample_caps_length():
    x = np.arange(1000.0)
    ds = downsample(x, max_points=100)
    assert len(ds) <= 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_summarize.py -v`
Expected: FAIL (`No module named 'ttio_mcp.summarize'`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ttio_mcp/summarize.py
"""Token-cheap summaries of numeric arrays for inline tool results."""
from __future__ import annotations

from typing import Any

import numpy as np


def array_summary(a: np.ndarray) -> dict[str, Any]:
    """Compact stats for a 1-D numeric array."""
    a = np.asarray(a)
    if a.size == 0:
        return {"count": 0}
    return {
        "count": int(a.size),
        "min": float(np.min(a)),
        "max": float(np.max(a)),
        "mean": float(np.mean(a)),
        "sum": float(np.sum(a)),
    }


def top_peaks(x: np.ndarray, y: np.ndarray, n: int = 10) -> list[dict[str, float]]:
    """Return the n highest-y points as {x-name omitted}: paired (mz,intensity)."""
    x = np.asarray(x)
    y = np.asarray(y)
    if x.size == 0:
        return []
    idx = np.argsort(y)[::-1][:n]
    return [{"mz": float(x[i]), "intensity": float(y[i])} for i in idx]


def downsample(a: np.ndarray, max_points: int = 200) -> list[float]:
    """Uniformly subsample a 1-D array to at most max_points for a preview."""
    a = np.asarray(a)
    if a.size <= max_points:
        return [float(v) for v in a]
    step = int(np.ceil(a.size / max_points))
    return [float(v) for v in a[::step]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_summarize.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ttio_mcp/summarize.py tests/test_summarize.py
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "feat: array summarization utilities"
```

### Task 13: export

**Files:**
- Create: `src/ttio_mcp/export.py`
- Test: `tests/test_export.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_export.py
from pathlib import Path

import numpy as np

from ttio_mcp.export import export_arrays


def test_export_parquet(tmp_path):
    p = export_arrays({"mz": np.array([1.0, 2.0]), "intensity": np.array([9.0, 8.0])},
                      out_dir=tmp_path, basename="spec1", fmt="parquet")
    assert Path(p).exists()
    assert p.endswith(".parquet")


def test_export_csv(tmp_path):
    p = export_arrays({"mz": np.array([1.0, 2.0]), "intensity": np.array([9.0, 8.0])},
                      out_dir=tmp_path, basename="spec1", fmt="csv")
    text = Path(p).read_text()
    assert "mz" in text and "intensity" in text


def test_export_json(tmp_path):
    p = export_arrays({"mz": np.array([1.0, 2.0])}, out_dir=tmp_path, basename="s", fmt="json")
    assert Path(p).read_text().strip().startswith("{")


def test_unequal_lengths_rejected_for_tabular(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        export_arrays({"a": np.array([1.0]), "b": np.array([1.0, 2.0])},
                      out_dir=tmp_path, basename="s", fmt="parquet")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_export.py -v`
Expected: FAIL (`No module named 'ttio_mcp.export'`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ttio_mcp/export.py
"""Full-fidelity array export to a local file; returns the path."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import numpy as np

_FORMATS = {"parquet", "csv", "json"}


def export_arrays(arrays: Mapping[str, np.ndarray], *, out_dir: Path, basename: str,
                  fmt: str = "parquet") -> str:
    """Write named 1-D arrays to out_dir/basename.<fmt>; return the path.

    parquet/csv require equal-length columns; json allows ragged arrays.
    """
    if fmt not in _FORMATS:
        raise ValueError(f"fmt must be one of {sorted(_FORMATS)}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = {k: np.asarray(v) for k, v in arrays.items()}

    if fmt == "json":
        path = out_dir / f"{basename}.json"
        path.write_text(json.dumps({k: v.tolist() for k, v in cols.items()}))
        return str(path)

    lengths = {len(v) for v in cols.values()}
    if len(lengths) > 1:
        raise ValueError(f"tabular export requires equal-length columns; got {lengths}")

    if fmt == "csv":
        path = out_dir / f"{basename}.csv"
        names = list(cols)
        rows = zip(*[cols[n] for n in names])
        with open(path, "w") as fh:
            fh.write(",".join(names) + "\n")
            for row in rows:
                fh.write(",".join(repr(float(x)) for x in row) + "\n")
        return str(path)

    # parquet
    import pyarrow as pa
    import pyarrow.parquet as pq
    path = out_dir / f"{basename}.parquet"
    table = pa.table({k: v for k, v in cols.items()})
    pq.write_table(table, path)
    return str(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_export.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ttio_mcp/export.py tests/test_export.py
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "feat: array export (parquet/csv/json)"
```

---

## Phase 9 — Data reading tools

### Task 14: dataset_summary / dataset_read / dataset_export

**Files:**
- Create: `src/ttio_mcp/tools/data.py`
- Modify: `src/ttio_mcp/server.py`
- Test: `tests/test_tools_data.py`

SDK contract (verified): `ttio.SpectralDataset.open(path)` context manager. `.title`, `.is_encrypted`, `.runs -> Mapping[str, Run]`, `.ms_runs`, `.subjects -> list[Subject]`, `.samples -> list[Sample]`, `.images -> dict[ImageKind, Image]`, `.image_for_kind(kind)`, `.identifications()`, `.quantifications()`, `.provenance()`. A run is indexable (`run[i] -> Spectrum`, `len(run)`); a `MassSpectrum` exposes `.mz_array.data` / `.intensity_array.data` (numpy). Generic `Spectrum.signal_array(name).data`.

The data tools read a **local** `.tio` path (typically produced by `ttio_download`). They never hit the network.

- [ ] **Step 1: Write the failing test**

This test builds a tiny real `.tio` via the library writer if available; if the writer API is not trivially callable, it falls back to monkeypatching `SpectralDataset.open`. Use the monkeypatch approach for determinism:

```python
# tests/test_tools_data.py
import asyncio
import contextlib

import numpy as np

from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.tools import data as dt


class _Sig:
    def __init__(self, arr):
        self.data = np.asarray(arr)


class _Spectrum:
    def __init__(self):
        self.mz_array = _Sig([100.0, 200.0, 300.0])
        self.intensity_array = _Sig([5.0, 50.0, 25.0])

    def signal_array(self, name):
        return {"mz": self.mz_array, "intensity": self.intensity_array}[name]

    def signal_array_names(self):
        return ["mz", "intensity"]


class _Run:
    def __len__(self): return 2
    def __getitem__(self, i): return _Spectrum()


class _DS:
    title = "demo"
    is_encrypted = False

    @property
    def runs(self): return {"run_0001": _Run()}
    ms_runs = {"run_0001": _Run()}

    @property
    def subjects(self): return [{"external_id": "S1"}]

    @property
    def samples(self): return [{"sample_kind": "plasma"}]

    def identifications(self): return []
    def quantifications(self): return []
    def provenance(self): return []


def _patch_open(monkeypatch):
    @contextlib.contextmanager
    def fake_open(path, **kw):
        yield _DS()
    monkeypatch.setattr(dt.SpectralDataset, "open", staticmethod(fake_open))


def _app(monkeypatch):
    _patch_open(monkeypatch)
    cm = ConnectionManager()
    app = FastMCP("t")
    dt.register(app, cm, Config.from_env())
    return app


def _call(app, name, **kw):
    res = app._tool_manager.get_tool(name).fn(**kw)
    return asyncio.run(res) if asyncio.iscoroutine(res) else res


def test_dataset_summary(monkeypatch, tmp_path):
    app = _app(monkeypatch)
    out = _call(app, "ttio_dataset_summary", path=str(tmp_path / "x.tio"))
    assert out["title"] == "demo"
    assert out["runs"]["run_0001"]["spectra"] == 2


def test_dataset_read_spectrum(monkeypatch, tmp_path):
    app = _app(monkeypatch)
    out = _call(app, "ttio_dataset_read", path=str(tmp_path / "x.tio"),
                what="spectrum", run="run_0001", index=0)
    assert out["top_peaks"][0]["mz"] == 200.0
    assert out["mz"]["count"] == 3


def test_dataset_read_subjects(monkeypatch, tmp_path):
    app = _app(monkeypatch)
    out = _call(app, "ttio_dataset_read", path=str(tmp_path / "x.tio"), what="subjects")
    assert out["subjects"][0]["external_id"] == "S1"


def test_dataset_export_spectrum(monkeypatch, tmp_path):
    app = _app(monkeypatch)
    out = _call(app, "ttio_dataset_export", path=str(tmp_path / "x.tio"),
                run="run_0001", index=0, out_dir=str(tmp_path), fmt="json")
    assert out["export_path"].endswith(".json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_data.py -v`
Expected: FAIL (`No module named 'ttio_mcp.tools.data'`)

- [ ] **Step 3: Write the implementation**

```python
# src/ttio_mcp/tools/data.py
"""Local .tio reading/extraction tools: summaries inline, full arrays via export."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ttio import SpectralDataset

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import ToolError, to_tool_error
from ttio_mcp.summarize import array_summary, downsample, top_peaks
from ttio_mcp.export import export_arrays

_READABLE = {"runs", "spectrum", "signal", "subjects", "samples",
             "images", "identifications", "quantifications", "provenance"}


def _spectrum(ds: Any, run: str, index: int):
    runs = ds.runs
    if run not in runs:
        raise ToolError(f"run {run!r} not found; available: {sorted(runs)}")
    r = runs[run]
    if index < 0 or index >= len(r):
        raise ToolError(f"index {index} out of range (run has {len(r)} spectra)")
    return r[index]


def register(app: FastMCP_, conn: ConnectionManager, config: Config) -> None:  # type: ignore[name-defined]
    async def _run(fn, *a, **k):
        return await asyncio.to_thread(fn, *a, **k)

    @app.tool()
    async def ttio_dataset_summary(path: str) -> dict:
        """Summarize a local .tio: title, encryption, runs (with spectrum counts), subject/sample counts."""
        def work():
            with SpectralDataset.open(path) as ds:
                runs = {name: {"spectra": len(r)} for name, r in ds.runs.items()}
                return {
                    "title": getattr(ds, "title", None),
                    "is_encrypted": bool(getattr(ds, "is_encrypted", False)),
                    "runs": runs,
                    "subject_count": len(ds.subjects),
                    "sample_count": len(ds.samples),
                }
        try:
            return await _run(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}

    @app.tool()
    async def ttio_dataset_read(path: str, what: str, run: str | None = None, index: int = 0,
                                signal: str | None = None, max_points: int = 200,
                                top_n: int = 10, limit: int = 100) -> dict:
        """Read part of a local .tio. what=runs|spectrum|signal|subjects|samples|images|
        identifications|quantifications|provenance. Returns compact summaries; use
        ttio_dataset_export for full arrays."""
        if what not in _READABLE:
            return {"error": f"what must be one of {sorted(_READABLE)}"}

        def work():
            with SpectralDataset.open(path) as ds:
                if what == "runs":
                    return {"runs": {n: {"spectra": len(r)} for n, r in ds.runs.items()}}
                if what == "spectrum":
                    if run is None:
                        raise ToolError("what=spectrum requires run=")
                    sp = _spectrum(ds, run, index)
                    mz = sp.mz_array.data
                    inten = sp.intensity_array.data
                    return {
                        "run": run, "index": index,
                        "mz": array_summary(mz),
                        "intensity": array_summary(inten),
                        "top_peaks": top_peaks(mz, inten, n=top_n),
                        "mz_preview": downsample(mz, max_points),
                    }
                if what == "signal":
                    if run is None or signal is None:
                        raise ToolError("what=signal requires run= and signal=")
                    sp = _spectrum(ds, run, index)
                    arr = sp.signal_array(signal).data
                    return {"run": run, "index": index, "signal": signal,
                            "summary": array_summary(arr), "preview": downsample(arr, max_points)}
                if what == "subjects":
                    return {"subjects": [_obj(s) for s in ds.subjects[:limit]]}
                if what == "samples":
                    return {"samples": [_obj(s) for s in ds.samples[:limit]]}
                if what == "images":
                    return {"images": sorted(str(k) for k in getattr(ds, "images", {}).keys())}
                if what == "identifications":
                    return {"identifications": [_obj(x) for x in ds.identifications()[:limit]]}
                if what == "quantifications":
                    return {"quantifications": [_obj(x) for x in ds.quantifications()[:limit]]}
                return {"provenance": [_obj(x) for x in ds.provenance()[:limit]]}
        try:
            return await _run(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}

    @app.tool()
    async def ttio_dataset_export(path: str, run: str, index: int = 0,
                                  out_dir: str | None = None, basename: str | None = None,
                                  fmt: str = "parquet") -> dict:
        """Export a spectrum's full arrays (all signal channels) to a file. fmt=parquet|csv|json."""
        target = Path(out_dir) if out_dir else config.export_dir

        def work():
            with SpectralDataset.open(path) as ds:
                sp = _spectrum(ds, run, index)
                names = sp.signal_array_names() if hasattr(sp, "signal_array_names") else ["mz", "intensity"]
                arrays = {n: sp.signal_array(n).data for n in names}
                bn = basename or f"{run}_{index}"
                return export_arrays(arrays, out_dir=target, basename=bn, fmt=fmt)
        try:
            p = await _run(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"export_path": p}


def _obj(o: Any) -> Any:
    if isinstance(o, dict):
        return o
    import dataclasses
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    return {k: getattr(o, k) for k in dir(o) if not k.startswith("_") and not callable(getattr(o, k))}
```

Fix the stray annotation: replace `FastMCP_` in the `register` signature with a proper import. At the top of the file add `from mcp.server.fastmcp import FastMCP` and change the signature to `def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:`.

- [ ] **Step 4: Register in `server.py`**

```python
    from ttio_mcp.tools import data as data_tools
    data_tools.register(app, CONN, CONFIG)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_data.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Run the full unit suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/ttio_mcp/tools/data.py src/ttio_mcp/server.py tests/test_tools_data.py
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "feat: local .tio reading/extraction tools"
```

---

## Phase 10 — Integration, docs, polish

### Task 15: opt-in live integration test against a running daemon

**Files:**
- Create: `tests/integration/test_live_smoke.py`
- Create: `tests/integration/__init__.py`

- [ ] **Step 1: Write the gated integration test**

```python
# tests/integration/test_live_smoke.py
"""Live smoke test against a running tti-workbench-server.

Skipped unless TTIO_MCP_LIVE=1 and TTIO_WB_URL + credentials are set.
Launch the daemon + bootstrap admin per the server repo's smoke scripts first.
"""
import os

import pytest

LIVE = os.environ.get("TTIO_MCP_LIVE") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="set TTIO_MCP_LIVE=1 to run live")


def test_login_and_list_containers():
    import ttio
    url = os.environ["TTIO_WB_URL"]
    token = os.environ.get("TTIO_WB_TOKEN")
    if token:
        client = ttio.connect(url, auth=ttio.BearerAuth(token, os.environ.get("TTIO_WB_USERNAME", "u")))
    else:
        client = ttio.connect(url, auth=ttio.PasswordTotpAuth(
            os.environ["TTIO_WB_USERNAME"], os.environ["TTIO_WB_PASSWORD"], os.environ["TTIO_WB_TOTP"]))
    page = client.containers().list(limit=5)
    assert hasattr(page, "containers")
```

- [ ] **Step 2: Verify it skips cleanly without a daemon**

Run: `.venv/bin/python -m pytest tests/integration -v`
Expected: 1 skipped.

- [ ] **Step 3: Commit**

```bash
git add tests/integration
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "test: opt-in live daemon smoke"
```

### Task 16: docs + CHANGELOG + lint

**Files:**
- Modify: `README.md`, `docs/configuration.md`, `docs/tools.md`, `CHANGELOG.md`

- [ ] **Step 1: Rewrite `docs/configuration.md`** to document the new env vars: `TTIO_WB_URL`, `TTIO_WB_TOKEN` (API key/bearer), `TTIO_WB_USERNAME`, `TTIO_MCP_EXPORT_DIR`, `TTIO_MCP_CACHE_DIR`, `TTIO_MCP_PAGE_SIZE`. Document the `ttio_login` interactive path and the headless API-key path.

- [ ] **Step 2: Rewrite `docs/tools.md`** with the full tool catalog grouped by domain (auth, containers, cohorts, jobs/pipelines, sessions, transfers, data), one line each with parameters, matching the registered tools.

- [ ] **Step 3: Rewrite `README.md`** intro to describe the server as a non-admin tti-workbench-server client (drop all `.mpgo`-file framing), with a quickstart (configure env, run `ttio-mcp`, call `ttio_login`).

- [ ] **Step 4: Add a `CHANGELOG.md` entry** under a new `0.9.0` heading summarizing the rewrite: workbench client, removed `.mpgo` catalog/keyring/signing/uploader, new tool set.

- [ ] **Step 5: Lint + final full test run**

Run: `.venv/bin/python -m ruff check src tests && .venv/bin/python -m pytest -q`
Expected: ruff clean; all tests pass. Fix any ruff findings inline.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/configuration.md docs/tools.md CHANGELOG.md
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit -m "docs: rewrite for workbench MCP server; changelog 0.9.0"
```

### Task 17: end-to-end MCP server boot check

- [ ] **Step 1: Confirm the server builds and lists all tools**

```bash
cd /home/toddw/TTIO-MCP-Server
.venv/bin/python -c "
import asyncio
from ttio_mcp.server import build_app
app = build_app()
names = sorted(t.name for t in asyncio.run(app.list_tools()))
print(len(names), 'tools'); [print(' -', n) for n in names]
"
```
Expected: ~24 tools listed across auth/containers/cohorts/jobs/sessions/transfers/data; no exceptions. Verify no admin tools (`users`, `groups`, `dashboard`, `delete`, `register`) appear.

- [ ] **Step 2: Commit any final fixes**

```bash
git -c user.name='Todd White' -c user.email='todd.white@thalion.global' commit --allow-empty -m "chore: verify full tool surface boots"
```

---

## Done criteria

- `pytest -q` green; `ruff check` clean.
- `build_app()` registers the full non-admin tool surface and **no** admin/delete tools.
- A configured API key (`TTIO_WB_URL` + `TTIO_WB_TOKEN`) auto-connects at startup; `ttio_login` works interactively.
- Live smoke (`TTIO_MCP_LIVE=1`) passes against a running daemon.
- Docs describe the new model; no `.mpgo`-file framing remains.
- Push from Windows git per project memory (not part of these tasks).
