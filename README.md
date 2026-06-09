# TTI-O MCP Server

An [MCP](https://modelcontextprotocol.io/) server that exposes
[TTI-O](https://github.com/DTW-Thalion/TTI-O) (`.mpgo`) file
capabilities to LLM clients via a **catalog pattern** — a SQL index
over files that stay in place on disk or in object storage.

The server speaks MCP over stdio, surfaces 14 tools (register, query,
spectrum read, quantifications, encrypt/decrypt, cloud push,
sign/verify, local uploader), and delegates every byte of
cryptography and I/O to the TTI-O Python package. Keys live
server-side under an env-configured keyring and never cross the MCP
wire — tools reference them by `key_id`.

## Status

| Milestone | Status | Summary |
|---|---|---|
| M1: Foundation & schema           | ✅ shipped | Package scaffolding, SQLAlchemy schema, Alembic baseline, `initialize` handshake. |
| M2: Catalog & file registration   | ✅ shipped | `ttio_register_file`, `ttio_list_files`, `ttio_get_file`, `ttio_reverify`. |
| M3: Query & spectra               | ✅ shipped | `ttio_search_identifications`, `ttio_get_run`, `ttio_get_spectrum`, `ttio_get_quantifications`. |
| M4: Cloud I/O                     | ✅ shipped | `s3://`, `https://`, `gs://`, `az://` URIs via fsspec; `TTIO_MCP_FSSPEC_KWARGS`. |
| M5: Keyring & encryption          | ✅ shipped | `ttio_encrypt_file`, `ttio_decrypt_file`, `TTIO_KEYRING_PATH`, `as_user` hardening. |
| M6: Cloud push + encrypt-on-upload | ✅ shipped | `ttio_push_file` — streams local `.mpgo` to `s3://`/`gs://`/`abfs://`, optional in-flight AES-256-GCM. |
| M7: HMAC-SHA256 signatures         | ✅ shipped | `ttio_sign_file` + `ttio_verify_signature` over every `signal_channels/*_values` dataset. |
| M8: MCP conformance                | ✅ shipped | End-to-end conformance suite via the real `mcp` Python client over stdio — all 13 tools, error envelope. |
| M9: TestPyPI release               | blocked   | Tag-driven GitHub Actions → TestPyPI publish. Gated on TTI-O M40a (PyPI wheels for `mpeg-o`); M40a is itself paused on upstream account verification. |
| M10: Local uploader                | ✅ shipped | `ttio_launch_uploader` spawns a same-host tkinter picker + progress window, stages files into `TTIO_MCP_INTAKE_DIR`. |

Current version: **0.8.0.dev0** (Alpha). 115 tests, ruff clean, SQLite
and Postgres-portable.

## Requirements

- Python 3.11 or 3.12
- git (runtime install resolves the `mpeg-o` dependency from the
  [TTI-O v1.1.1 git tag](https://github.com/DTW-Thalion/TTI-O);
  PyPI publish tracked as TTI-O M40)

## Install

```bash
git clone https://github.com/DTW-Thalion/TTIO-MCP-Server.git
cd TTIO-MCP-Server
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"              # add ,cloud for s3fs/fsspec
```

The `cloud` extra (`s3fs`, `fsspec`) is only needed to register or
read files via cloud URIs — purely local workflows don't need it.

## Bootstrap the catalog

```bash
export TTIO_MCP_DB_URL="sqlite:///ttio_mcp.db"   # default if unset
alembic upgrade head
```

This creates the seven catalog tables (`users`, `files`, `studies`,
`runs`, `identifications`, `quantifications`, `provenance_records`)
and seeds a `system` user. `alembic downgrade base` reverses every
migration.

## Run

```bash
ttio-mcp
```

The server speaks MCP over stdio. Typical wire-up with Claude Code:

```bash
claude mcp add ttio-mcp -- ttio-mcp
```

For cloud credentials and keyring setup, export the relevant env vars
in the shell that launches `ttio-mcp` (see
[docs/configuration.md](docs/configuration.md)).

## Tools at a glance

| Tool | Purpose |
|---|---|
| `ttio_register_file`         | Hash a `.mpgo` URI, open it, extract metadata, upsert catalog rows. |
| `ttio_list_files`            | Paginated catalog listing with filters. |
| `ttio_get_file`              | Full record for one file (by id or uri) including studies and runs. |
| `ttio_reverify`              | Re-hash the referenced bytes; reports `drift=true` if changed. |
| `ttio_search_identifications`| Cross-file identification search (chebi, score, acquisition mode, …). |
| `ttio_get_run`               | Per-run detail with inline identifications and matching quantifications. |
| `ttio_get_spectrum`          | Lazy spectrum read from disk; downsamples past `max_points`. |
| `ttio_get_quantifications`   | Per-file quantification listing with filters. |
| `ttio_encrypt_file`          | In-place AES-256-GCM intensity encryption (local files only). |
| `ttio_decrypt_file`          | In-place decrypt back to plaintext (local files only). |
| `ttio_push_file`             | Upload a local `.mpgo` to a cloud URI, optionally encrypting on the way. |
| `ttio_sign_file`             | Sign every signal-channel dataset with HMAC-SHA256 (local files only). |
| `ttio_verify_signature`      | Verify every signed dataset; returns per-dataset verdicts plus an aggregate `valid`. |
| `ttio_launch_uploader`       | Open a local tkinter file-picker + progress window and copy the chosen file into `TTIO_MCP_INTAKE_DIR`. |

Full schemas, error codes, and response shapes: [docs/tools.md](docs/tools.md).

## Configuration

| Env var | Purpose |
|---|---|
| `TTIO_MCP_DB_URL`           | SQLAlchemy URL for the catalog. Default `sqlite:///ttio_mcp.db`. |
| `TTIO_MCP_FSSPEC_KWARGS`    | JSON object merged into every `fsspec.open` call for cloud URIs. |
| `TTIO_KEYRING_PATH`         | Filesystem path to the JSON keyring for encrypt/decrypt. |
| `TTIO_MCP_INTAKE_DIR`       | Directory where `ttio_launch_uploader` stages files picked by the user. |

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
pytest -q                                    # 115 tests
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
- [HANDOFF-M6.md](HANDOFF-M6.md) — cloud push + encrypt-on-upload
- [HANDOFF-M7.md](HANDOFF-M7.md) — HMAC-SHA256 dataset signatures
- [HANDOFF-M8.md](HANDOFF-M8.md) — MCP conformance suite

## License

Apache-2.0 — see [LICENSE](LICENSE).
