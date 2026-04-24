# MPEG-O MCP Server

An [MCP](https://modelcontextprotocol.io/) server that exposes
[MPEG-O](https://github.com/DTW-Thalion/MPEG-O) (`.mpgo`) file
capabilities to LLM clients via a **catalog pattern** — a SQL index
over files that stay in place on disk or in object storage.

The server speaks MCP over stdio, surfaces 10 tools (register, query,
spectrum read, quantifications, encrypt/decrypt), and delegates every
byte of cryptography and I/O to the MPEG-O Python package. Keys live
server-side under an env-configured keyring and never cross the MCP
wire — tools reference them by `key_id`.

## Status

| Milestone | Status | Summary |
|---|---|---|
| M1: Foundation & schema           | ✅ shipped | Package scaffolding, SQLAlchemy schema, Alembic baseline, `initialize` handshake. |
| M2: Catalog & file registration   | ✅ shipped | `mpgo_register_file`, `mpgo_list_files`, `mpgo_get_file`, `mpgo_reverify`. |
| M3: Query & spectra               | ✅ shipped | `mpgo_search_identifications`, `mpgo_get_run`, `mpgo_get_spectrum`, `mpgo_get_quantifications`. |
| M4: Cloud I/O                     | ✅ shipped | `s3://`, `https://`, `gs://`, `az://` URIs via fsspec; `MPGO_MCP_FSSPEC_KWARGS`. |
| M5: Keyring & encryption          | ✅ shipped | `mpgo_encrypt_file`, `mpgo_decrypt_file`, `MPGO_KEYRING_PATH`, `as_user` hardening. |
| M6: Conformance + publish         | planned   | MCP conformance suite, TestPyPI release. |

Current version: **0.5.0.dev0** (Alpha). 68 tests, ruff clean, SQLite
and Postgres-portable.

## Requirements

- Python 3.11 or 3.12
- git (runtime install resolves the `mpeg-o` dependency from the
  [MPEG-O v1.1.1 git tag](https://github.com/DTW-Thalion/MPEG-O);
  PyPI publish tracked as MPEG-O M40)

## Install

```bash
git clone https://github.com/DTW-Thalion/MPEG-O-MCP-Server.git
cd MPEG-O-MCP-Server
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"              # add ,cloud for s3fs/fsspec
```

The `cloud` extra (`s3fs`, `fsspec`) is only needed to register or
read files via cloud URIs — purely local workflows don't need it.

## Bootstrap the catalog

```bash
export MPGO_MCP_DB_URL="sqlite:///mpeg_o_mcp.db"   # default if unset
alembic upgrade head
```

This creates the seven catalog tables (`users`, `files`, `studies`,
`runs`, `identifications`, `quantifications`, `provenance_records`)
and seeds a `system` user. `alembic downgrade base` reverses every
migration.

## Run

```bash
mpeg-o-mcp
```

The server speaks MCP over stdio. Typical wire-up with Claude Code:

```bash
claude mcp add mpeg-o-mcp -- mpeg-o-mcp
```

For cloud credentials and keyring setup, export the relevant env vars
in the shell that launches `mpeg-o-mcp` (see
[docs/configuration.md](docs/configuration.md)).

## Tools at a glance

| Tool | Purpose |
|---|---|
| `mpgo_register_file`         | Hash a `.mpgo` URI, open it, extract metadata, upsert catalog rows. |
| `mpgo_list_files`            | Paginated catalog listing with filters. |
| `mpgo_get_file`              | Full record for one file (by id or uri) including studies and runs. |
| `mpgo_reverify`              | Re-hash the referenced bytes; reports `drift=true` if changed. |
| `mpgo_search_identifications`| Cross-file identification search (chebi, score, acquisition mode, …). |
| `mpgo_get_run`               | Per-run detail with inline identifications and matching quantifications. |
| `mpgo_get_spectrum`          | Lazy spectrum read from disk; downsamples past `max_points`. |
| `mpgo_get_quantifications`   | Per-file quantification listing with filters. |
| `mpgo_encrypt_file`          | In-place AES-256-GCM intensity encryption (local files only). |
| `mpgo_decrypt_file`          | In-place decrypt back to plaintext (local files only). |

Full schemas, error codes, and response shapes: [docs/tools.md](docs/tools.md).

## Configuration

| Env var | Purpose |
|---|---|
| `MPGO_MCP_DB_URL`           | SQLAlchemy URL for the catalog. Default `sqlite:///mpeg_o_mcp.db`. |
| `MPGO_MCP_FSSPEC_KWARGS`    | JSON object merged into every `fsspec.open` call for cloud URIs. |
| `MPGO_KEYRING_PATH`         | Filesystem path to the JSON keyring for encrypt/decrypt. |

Details and examples: [docs/configuration.md](docs/configuration.md).

## Response envelope

Every tool reply is a single `TextContent` whose body is JSON:

```jsonc
// success
{"ok": true,  "data": { /* tool-specific */ }}

// error
{"ok": false, "error": {"code": "not_found", "message": "..."}}
```

Error codes are stable per tool contract — see [docs/tools.md](docs/tools.md).

## Development

```bash
pytest -q                                    # 68 tests
ruff check .
alembic upgrade head && alembic downgrade base   # round-trip
```

CI runs the same commands across Python 3.11 and 3.12 on Ubuntu
(`.github/workflows/ci.yml`).

## Deployment

[DEPLOYMENT-GUIDE.md](DEPLOYMENT-GUIDE.md) walks through install,
configuration, database bootstrap, client wire-up, and common
troubleshooting in layperson terms. Read it if this is the first
machine you're bringing up.

## Milestone handoffs

Each milestone ships with a `HANDOFF-M<n>.md` capturing scope,
binding decisions, and acceptance criteria at the time the milestone
was delivered. They are kept for historical context and are not
retroactively edited.

- [HANDOFF.md](HANDOFF.md) — M1 foundation & schema
- [HANDOFF-M2.md](HANDOFF-M2.md) — catalog & registration
- [HANDOFF-M3.md](HANDOFF-M3.md) — query & spectra
- [HANDOFF-M4.md](HANDOFF-M4.md) — cloud I/O
- [HANDOFF-M5.md](HANDOFF-M5.md) — keyring & encryption

## License

Apache-2.0 — see [LICENSE](LICENSE).
