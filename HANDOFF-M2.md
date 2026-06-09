# HANDOFF-M2.md — TTI-O-MCP M2: Catalog & File Registration

## Context

M1 shipped the scaffolding: six tables migrated, server handshakes
`initialize`, zero tools registered. M2 turns the server into something
useful — register `.mpgo` files, extract their metadata into the
catalog, and expose four MCP tools for catalog operations.

M1 HANDOFF lives at [HANDOFF.md](HANDOFF.md) — still the reference for
binding decisions (auth, secrets, extraction policy, tool granularity).

## M2 Scope

- **Checksum helpers** (`ttio_mcp.hashes`). Stream the whole file for
  `file_sha256`. `content_sha256` is an alias for `file_sha256` in M2;
  true content-semantic hashing (stable under timestamp / signature
  attribute churn) is deferred to a later milestone. Column stays for
  forward compat.
- **Extractor** (`ttio_mcp.catalog`). Open a file through
  `mpeg_o.SpectralDataset.open`, harvest `title`,
  `isa_investigation_id`, `feature_flags`, MS/NMR runs with acquisition
  mode + instrument metadata, identifications, dataset-level
  provenance records. Populate `files`, `studies`, `runs`,
  `identifications`, `provenance_records` in **one transaction** per
  `register` call.
- **Four MCP tools**, one per concept (no tool sprawl):
  1. `ttio_register_file(uri, display_name?, as_user?)` — resolve →
     hash → open → extract → insert. Idempotent on `uri`: re-register
     updates the file row + replaces child rows atomically.
  2. `ttio_list_files(limit?, offset?, title_contains?, acquisition_mode?)`
     — paginated catalog listing with light filters.
  3. `ttio_get_file(id_or_uri)` — full file record + child counts.
  4. `ttio_reverify(id_or_uri)` — re-hash the referenced bytes and
     update `last_verified_at`; flag drift if `file_sha256` changed.
- **Local URIs only**: `file://` and bare paths. `s3://` / `https://`
  land in M4 with the secrets work.
- **Sync I/O under a thread**: `SpectralDataset.open` is sync; tool
  handlers run it via `asyncio.to_thread`.
- **Fixtures**: tests build their own `.mpgo` files via
  `SpectralDataset.write_minimal` in a fixture helper. No vendored
  binary blobs in the repo.

## Out of Scope for M2

- Spectrum / signal-array reads (M3 — query tools).
- Quantifications, features, transitions (M3).
- Remote URIs, keyring, cloud I/O, encryption, fsspec config (M4).
- True content-semantic `content_sha256` distinct from `file_sha256`
  (deferred — column kept).
- Pagination cursors, streaming responses, resource URIs (M3+).

## Package Layout (new/changed in M2)

```
src/ttio_mcp/
├── hashes.py                # NEW — file_sha256 / content_sha256 helpers
├── catalog.py               # NEW — register_file + extract helpers
├── tools/
│   ├── __init__.py          # NEW — register all tool handlers on a Server
│   ├── _helpers.py          # NEW — as_user resolution, URI parsing, errors
│   ├── register.py          # NEW
│   ├── list_files.py        # NEW
│   ├── get_file.py          # NEW
│   └── reverify.py          # NEW
└── server.py                # UPDATED — call tools.register(server)
```

## Tool Contracts (rough)

Each tool:
- Accepts JSON arguments exactly as declared. Unknown arguments
  rejected.
- Returns a single `TextContent` block whose text is a JSON object
  `{"ok": true, "data": ...}` or `{"ok": false, "error": {...}}`. No
  mixed-content responses in M2.
- Accepts an optional `as_user` argument (no-op pending M4 auth —
  documented in each schema).

### `ttio_register_file`

```json
{
  "type": "object",
  "properties": {
    "uri":          {"type": "string", "description": "file:// or bare path"},
    "display_name": {"type": "string"},
    "as_user":      {"type": "string", "description": "reserved for M4 auth"}
  },
  "required": ["uri"]
}
```

Response `data`:
```json
{
  "file_id": 42,
  "uri": "file:///...",
  "file_sha256": "...",
  "format_version": "1.3",
  "features": ["base_v1", ...],
  "counts": {"studies": 1, "runs": 2, "identifications": 3, "provenance_records": 1},
  "was_update": false
}
```

### `ttio_list_files`

```json
{
  "type": "object",
  "properties": {
    "limit":             {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
    "offset":            {"type": "integer", "minimum": 0, "default": 0},
    "title_contains":    {"type": "string"},
    "acquisition_mode":  {"type": "string", "description": "exact match, e.g. DDA"}
  }
}
```

### `ttio_get_file`

Accepts exactly one of `id` (integer) or `uri` (string).

### `ttio_reverify`

Same input as `get_file`. Response includes `drift` boolean.

## Error Model

Tool handlers return `{"ok": false, "error": {"code", "message"}}`:

- `not_found` — no file row matches.
- `invalid_uri` — scheme not supported in M2.
- `resolve_failed` — path doesn't exist / can't read.
- `not_mpgo` — file isn't a valid `.mpgo` (TTI-O raises on open).
- `duplicate_uri` — race on registration (shouldn't happen with
  upsert; defensive).
- `internal` — fallback; carries truncated exception repr.

Never raise out of a tool handler — the MCP SDK will turn it into a
protocol-level error and the user loses the structured code.

## Acceptance Checklist

- [ ] `ttio_register_file` round-trips a `write_minimal` fixture:
      row in `files`, matching `studies` / `runs` / `identifications` /
      `provenance_records`, `format_version` is correct.
- [ ] Re-registering the same URI updates in place; children replaced
      atomically; no duplicate rows.
- [ ] `ttio_list_files` paginates and filters.
- [ ] `ttio_get_file` returns counts matching the DB.
- [ ] `ttio_reverify` marks `last_verified_at`, sets `drift=false` on
      an unchanged file, `drift=true` when the file bytes change.
- [ ] `pytest -q` 100% green, at least one test per tool + one
      round-trip + one idempotency test.
- [ ] `ruff check .` clean.
- [ ] Full M1 acceptance checklist still green (handshake, schema
      round-trip).
- [ ] CHANGELOG entry for `[0.2.0.dev0]`.

## Workflow

Direct commits to `main`, `[M2] ...` prefix, one logical change each.
Push via Windows git against `//wsl.localhost/...` (WSL git hangs on
HTTPS auth per the project memory).
