# HANDOFF.md — MPEG-O-MCP M1: Foundation & Schema

## Context

Bootstrap `MPEG-O-MCP`, an MCP server that exposes `.mpgo` file capabilities
to LLM clients via the catalog pattern (SQL index over files that stay in
place). This is milestone 1 of 5; scope is scaffolding only — no tool
logic. The deliverable is a repo that migrates its schema, launches, and
survives an MCP `initialize` handshake.

Main MPEG-O repo: `github.com/DTW-Thalion/MPEG-O`, currently at tag
`v1.0.0` (first stable release, 2026-04-23). Python package name
`mpeg-o` (imports as `mpeg_o`) — **not yet published to PyPI or
TestPyPI**; M1 installs the dependency directly from the git tag.
Publishing is tracked as MPEG-O M40, planned for v1.0.1.

## Binding Decisions

1. **Language:** Python 3.11+; Anthropic `mcp` SDK.
2. **DB:** SQLite for v0.1, Postgres-portable SQLAlchemy 2.x schema. Alembic
   for migrations.
3. **License:** Apache-2.0.
4. **Transport:** stdio only in v0.1.
5. **File refs:** canonical URI (`file://`, `s3://`, `https://`) + two
   checksums — `file_sha256` (whole-file) and `content_sha256` (canonical
   signed-content hash).
6. **Extraction policy (M2/M3):** eager by default (runs + provenance +
   identifications). Spectrum signals are never indexed in the DB.
7. **Auth forward-compat:** `users` table created now (empty except a seeded
   `system` user). `registered_by` / `owner_user_id` FKs on `files`. All
   MCP tools will accept an optional `as_user` argument (no-op in v0.1).
8. **Secrets:** env-based only (`MPGO_KEYRING_PATH`, AWS env vars,
   `MPGO_FSSPEC_CONFIG_PATH`). Never in MCP tool args. (M4 work; mentioned
   so you don't design them out.)
9. **Tool granularity (M2/M3):** one tool per concept with rich schema; no
   tool sprawl.

## M1 Scope

Repo scaffolding: package layout, SQLAlchemy models, Alembic baseline
migration, empty MCP server with console entry point, CI, and tests that
verify (a) the schema migrates and seeds, (b) the server handshakes.

## Repository Layout

```
MPEG-O-MCP/
├── pyproject.toml
├── README.md
├── LICENSE                       # Apache-2.0 full text
├── CHANGELOG.md
├── alembic.ini
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/<rev>_initial_schema.py
├── src/mpeg_o_mcp/
│   ├── __init__.py               # __version__ = "0.1.0.dev0"
│   ├── server.py                 # MCP server + main() entry point
│   ├── config.py                 # env var loading, defaults
│   └── db/
│       ├── __init__.py
│       ├── models.py             # declarative Base + all tables
│       └── session.py            # engine / session factory
├── tests/
│   ├── __init__.py
│   ├── conftest.py               # in-memory engine, session fixtures
│   ├── test_schema.py
│   └── test_initialize.py
└── .github/workflows/ci.yml
```

## pyproject.toml

- Build backend: hatchling.
- Name `mpeg-o-mcp`, import `mpeg_o_mcp`, Python `>=3.11`.
- Runtime deps: `mcp>=1.0`, `sqlalchemy>=2.0`, `alembic>=1.13`, plus
  `mpeg-o` pulled directly from the v1.0.0 git tag via a PEP 508
  direct-URL reference:
  ```
  mpeg-o @ git+https://github.com/DTW-Thalion/MPEG-O.git@v1.0.0#subdirectory=python
  ```
  When MPEG-O M40 lands on PyPI (v1.0.1 target), swap this to
  `mpeg-o>=1.0,<2.0`.
- Extras:
  - `dev`: pytest, pytest-asyncio, ruff, mypy
  - `cloud`: s3fs, fsspec (pass-through; actual use in M4)
- Console script: `mpeg-o-mcp = mpeg_o_mcp.server:main`.
- SPDX `Apache-2.0`.
- pytest config: `asyncio_mode = "auto"`.

No `--extra-index-url` needed — the git-URL dependency resolves
directly from GitHub on both `pip install` and CI.

## Database Schema

All tables have `id Integer PK autoincrement` unless noted. Timestamps are
`DateTime(timezone=True)`. JSON columns use SQLAlchemy's portable `JSON`
type. Use `metadata_json` (not `metadata`) to avoid shadowing the
Declarative Base attribute.

**`users`** — `id`, `name` (unique, not null), `created_at`. Seed one row
in the initial migration: `{id: 1, name: "system"}`.

**`files`**
- `uri` (String, not null, **unique**, indexed) — canonical URI
- `display_name` (String, nullable)
- `file_sha256` (String(64), not null, indexed)
- `content_sha256` (String(64), not null, indexed)
- `format_version` (String, not null)
- `features` (JSON, not null, default `{}`)
- `encrypted` (Boolean, not null, default False)
- `signed` (Boolean, not null, default False)
- `registered_at` (timestamp, server_default now)
- `last_verified_at` (timestamp, nullable)
- `registered_by` (FK `users.id`, not null, default 1)
- `owner_user_id` (FK `users.id`, nullable)

**`studies`** — `file_id` (FK, cascade delete), `title` (indexed, nullable),
`isa_investigation_id` (nullable), `metadata_json` (JSON, default `{}`).

**`runs`** — `file_id` (FK, cascade), `name` (not null),
`acquisition_mode` (indexed, nullable — e.g. "DDA", "DIA", "1D-NMR"),
`spectrum_count` (Integer, default 0), `instrument_manufacturer`
(indexed, nullable), `instrument_model` (nullable), `polarity` (nullable),
`metadata_json`.

**`identifications`** — `file_id` (FK, cascade), `run_id` (FK `runs.id`,
cascade), `chebi_id` (indexed, nullable), `name` (nullable), `score`
(Float, indexed, nullable), `spectrum_index` (Integer, nullable),
`metadata_json`. Composite index `(chebi_id, score)`.

**`provenance_records`** — `file_id` (FK, cascade), `software` (not null),
`timestamp` (nullable), `input_refs` (JSON, default `[]`), `output_refs`
(JSON, default `[]`), `metadata_json`.

## Alembic

- `alembic init migrations`; edit `migrations/env.py` to import
  `mpeg_o_mcp.db.models.Base` and set `target_metadata = Base.metadata`.
- Generate: `alembic revision --autogenerate -m "initial schema"`.
- Hand-edit the generated file to append a data migration inside
  `upgrade()` that inserts the seed `system` user via `op.bulk_insert`.
  Mirror the deletion in `downgrade()`.
- Default DB URL: `sqlite:///mpeg_o_mcp.db` in cwd. Override via
  `MPGO_MCP_DB_URL`. `env.py` must read that env var if set.

## MCP Server Skeleton (`server.py`)

- Use the official `mcp` SDK. Before coding, run
  `python -c "import mcp, pkgutil; print(mcp.__version__); print([m.name for m in pkgutil.iter_modules(mcp.__path__)])"`
  and read the installed examples to confirm current API shape. Do not
  guess module paths from memory.
- Server name `mpeg-o-mcp`, version from `mpeg_o_mcp.__version__`.
- Register **zero tools** in M1.
- `async def serve()` runs the stdio transport; `def main()` is a thin
  `asyncio.run(serve())` wrapper bound to the console script.
- Let the SDK handle `initialize` with defaults.

## Tests

**`conftest.py`** — `engine` fixture: in-memory SQLite with
`Base.metadata.create_all`. `session` fixture: scoped session.

**`test_schema.py`**
- All six tables exist on a fresh in-memory DB.
- Insert one row in each table respecting FKs; verify cascade delete
  removes studies/runs/identifications/provenance when the parent `files`
  row is deleted.
- Run `alembic upgrade head` against a tempfile SQLite URL; query
  `sqlite_master` for the table list; assert the `system` user row is
  present. Then `alembic downgrade base` and assert tables are gone.

**`test_initialize.py`**
- Spawn `mpeg-o-mcp` as a subprocess.
- Use the `mcp` SDK's stdio client to send `initialize`.
- Assert response has `protocolVersion`, `capabilities`, and
  `serverInfo.name == "mpeg-o-mcp"`.
- Close cleanly; assert exit code 0.

## GitHub Actions (`.github/workflows/ci.yml`)

- Triggers: `push`, `pull_request`.
- Matrix: `python-version: ["3.11", "3.12"]`, `ubuntu-latest`.
- Steps:
  1. `actions/checkout@v4`
  2. `actions/setup-python@v5` with pip cache keyed on `pyproject.toml`.
  3. `pip install -e ".[dev]"` (no extra-index-url — `mpeg-o` resolves
     from its git tag per the direct-URL dependency in `pyproject.toml`).
  4. `ruff check .`
  5. `alembic upgrade head` (env `MPGO_MCP_DB_URL=sqlite:///./ci.db`).
  6. `pytest -q`.

## Acceptance Checklist

- [ ] Fresh clone → `pip install -e ".[dev]"` succeeds (pulls
      `mpeg-o` from the MPEG-O v1.0.0 git tag; no extra-index-url
      required).
- [ ] `alembic upgrade head` creates all six tables and seeds the
      `system` user.
- [ ] `alembic downgrade base` cleanly drops everything.
- [ ] `mpeg-o-mcp` launches, handshakes `initialize`, exits cleanly on
      EOF.
- [ ] `pytest -q` green locally and in CI on both Python 3.11 and 3.12.
- [ ] `ruff check .` clean.
- [ ] README documents install, alembic bootstrap, and a placeholder
      `claude mcp add` snippet (real tool list comes in M2).
- [ ] LICENSE is full Apache-2.0 text. CHANGELOG has a `[0.1.0.dev0]`
      entry noting "Initial scaffolding (M1)."

## Out of Scope for M1

Do **not** implement any of the following — each belongs to a later
milestone. If you find yourself writing one, stop and flag:

- `mpgo_*` tool handlers of any kind (M2/M3).
- Checksum computation helpers (M2).
- Keyring loading, fsspec config, encryption, cloud I/O (M4).
- MCP conformance suite beyond the `initialize` smoke test (M5).
- TestPyPI publish workflow (M5).
- Fetching MPEG-O fixture files (M2 will add them).

## Workflow

- Branch `m1-foundation`. Commits prefixed `[M1] ...`, one logical change
  each.
- Single PR titled `M1: Foundation & Schema`. Paste the acceptance
  checklist into the PR body with each box checked.
- Before requesting review, run the full acceptance checklist locally and
  confirm CI is green.
