# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `mpgo_launch_uploader` tool — the server spawns a local tkinter
  file-picker on the user's desktop (same-machine stdio deployment)
  and copies the chosen file into `MPGO_MCP_INTAKE_DIR`. Bridges the
  "user has a binary file, wants it in the catalog" gap without
  pasting bytes through the chat or prearranging a URI.
  - New module tree: `src/mpeg_o_mcp/uploader/{__init__,core,gui,__main__}.py`.
    `core.py` holds all pure logic (format detection, intake
    resolution, collision-safe copy); `gui.py` lazily imports
    tkinter; `__main__.py` is the JSON-emitting subprocess entry
    point.
  - New env var `MPGO_MCP_INTAKE_DIR` surfaces through `Config` and
    `docs/configuration.md`. Unset → `intake_not_configured`.
  - Collision rule: `sample.mpgo` →
    `sample-20260424T120000Z.mpgo` → `sample-20260424T120000Z-1.mpgo`.
    Injectable clock keeps the test deterministic.
  - Subprocess pattern (`subprocess.run` with `timeout_seconds`,
    default 600) keeps the MCP loop responsive and lets the tool
    surface `timeout`, `cancelled`, `no_display`, `upload_failed` as
    stable `error.code` values.
  - `scripts/try_uploader.py` drives the subprocess end-to-end for
    manual smoke-testing on a host with a display.
  - Tool surface grows from 13 → 14; M3, M8 name sets updated.
- 24 new tests (`tests/test_uploader_core.py`,
  `tests/test_launch_uploader_tool.py`) — mock `subprocess.run` so
  CI doesn't need a display. Total: 112 passing.
- Upload progress window. The picker hand-off now opens a
  determinate `ttk.Progressbar` that tracks the copy into
  `MPGO_MCP_INTAKE_DIR`, with live `%` and MiB read-out. Core gains
  a `progress=(copied, total)` callback and a chunked
  `_copy_with_progress` loop (1 MiB chunks, replaces the previous
  `shutil.copy2`); the tkinter wrapper drives the bar from a worker
  thread via `queue.Queue` + `root.after`. Partial destinations are
  cleaned up on mid-stream failure. 3 new core tests; total: 115
  passing.

### Changed
- Documentation tune-up following M8.
  - `README.md`: M9 row marked **blocked** (upstream MPEG-O M40a PyPI
    publish is itself paused on account-verification).
  - `docs/configuration.md`: keyring rules now describe both supported
    algorithms (`AES-256-GCM` at 32 bytes and `hmac-sha256`
    variable-length) and the cross-algorithm `algorithm_mismatch`
    guarantee. Transport section drops the stale "v0.1" marker and
    calls out that MCP-over-HTTP / SSE are not implemented.
  - `DEPLOYMENT-GUIDE.md`: test-count line bumped 84 → 88 to match M8.

## [0.8.0.dev0] — 2026-04-24

### Added
- MCP conformance test suite (M8).
  - New `tests/test_m8_conformance.py` drives the real
    `mpeg-o-mcp` subprocess via the `mcp` Python client SDK
    (`stdio_client` + `ClientSession`) rather than calling tool
    handlers in-process. Every prior test suite exercises the
    handlers directly; M8 proves the server works as an actual MCP
    server: JSON-RPC 2.0 over stdio, `initialize` handshake, valid
    `tools/list` shape, and `tools/call` round-trips through the
    `{"ok", "data"|"error"}` text envelope.
  - Four tests cover:
    - `initialize` handshake + `list_tools` (all 13 tool names
      present; every schema has `type=object`, rejects
      `additionalProperties`, and declares `properties`).
    - Linear happy path across 12 of 13 tools on a local MS
      fixture: `register_file` → `list_files` → `get_file` →
      `get_run` → `search_identifications` →
      `get_quantifications` → `get_spectrum` (plaintext) →
      `sign_file` → `verify_signature` → `reverify` →
      `encrypt_file` → `get_spectrum` (with `key_id`, encrypted) →
      `decrypt_file`. State accumulates inside one subprocess —
      the catalog row transitions through real `signed` /
      `encrypted` flips.
    - `mpgo_push_file` end-to-end against the shared
      `ThreadedMotoServer` S3 fixture (skipped when the `cloud`
      extras aren't installed).
    - Structured error envelope — a lookup by a nonexistent id
      returns `{"ok": false, "error": {"code": "not_found", ...}}`
      on the wire.
  - The subprocess is booted fresh per test; env vars
    (`MPGO_MCP_DB_URL`, `MPGO_KEYRING_PATH`, optionally
    `MPGO_MCP_FSSPEC_KWARGS`) flow through `StdioServerParameters`
    so tests are hermetic.
  - No new runtime dependencies. `mcp` was already a direct dep.

### Tests
- 4 new tests in `tests/test_m8_conformance.py`. One is conditionally
  skipped on machines without moto / s3fs — the S3 conformance step
  is only meaningful with the cloud fixture available. Suite total:
  **88 tests** (was 84).

## [0.7.0.dev0] — 2026-04-24

### Added
- HMAC-SHA256 dataset signatures (M7).
  - New `mpgo_sign_file` tool. Resolves a local `.mpgo` to a path,
    loads an `hmac-sha256` key from the server-side keyring by
    `key_id`, opens the file via h5py in `r+` mode, walks every
    `signal_channels/*_values` dataset under both MS and NMR runs,
    and calls `mpeg_o.signatures.sign_dataset` on each. Emits the
    canonical v2 HMAC tag into each dataset's `@mpgo_signature`
    attribute. Re-signing overwrites the existing attribute.
  - New `mpgo_verify_signature` tool. Walks every dataset with an
    `@mpgo_signature` attribute and verifies each under the
    referenced key, returning per-dataset verdicts plus an
    aggregate `valid` flag. An unsigned file raises the new
    `not_signed` error code so callers can't mistake "no signed
    datasets" for "verified successfully".
  - Keyring is now algorithm-aware. `Keyring.get(key_id,
    expected_algorithm=...)` enforces that the stored `algorithm`
    field matches the caller's intent (new `algorithm_mismatch`
    error code). The hardcoded 32-byte length check is gone;
    `AES-256-GCM` still requires 32 bytes, but `hmac-sha256` keys
    are variable-length (non-empty, with any reasonable length).
    `mpgo_encrypt_file` / `mpgo_decrypt_file` / `mpgo_get_spectrum`
    / `mpgo_push_file` now pin their keys to `AES-256-GCM`, so
    signing keys can't be accidentally used for encryption and
    vice-versa.
  - Alembic migration `3840d96e5185_signature_columns` adds three
    nullable columns to `files`: `signature_algorithm`,
    `signed_at`, `signed_by` (FK → `users.id`). The pre-existing
    boolean `signed` column now reflects signing state.
  - Cloud URIs and encrypted files are rejected up front for both
    new tools — signing requires plaintext byte layout. The manual
    sign-before-push workflow (sign local, then `mpgo_push_file`)
    stays; DEPLOYMENT-GUIDE.md covers it.

### Tests
- 7 new tests in `tests/test_m7_sign_verify.py` (sign/verify
  round-trip, wrong-key fails, unsigned file raises, AES key
  rejected for signing, encrypted file rejected for signing, remote
  URI rejected for sign and verify). Plus 3 new keyring tests in
  `tests/test_m5_keyring.py` for the HMAC-SHA256 algorithm and the
  new `expected_algorithm` guard. Suite total: **84 tests** (was 74).
- `test_tools_surface_has_all_11` renamed to
  `test_tools_surface_has_all_13` and gains `mpgo_sign_file` +
  `mpgo_verify_signature`.

## [0.6.0.dev0] — 2026-04-24

### Added
- Cloud push + encrypt-on-upload (M6).
  - New `mpgo_push_file` tool. Streams a local `.mpgo` to a writable
    cloud URI (`s3://`, `gs://`, `gcs://`, `abfs://`, `abfss://`,
    `az://`) via fsspec, then registers the uploaded object in the
    catalog through the normal `register_file` path. The local source
    is never modified.
  - Optional `key_id` argument enables in-flight AES-256-GCM
    encryption: a throwaway temp copy is encrypted locally using the
    existing M5 `SpectralDataset.encrypt_with_key` code path, only
    the ciphertext is uploaded, and the catalog row is marked
    `encrypted=true` with `encrypted_algorithm="AES-256-GCM"`. The
    `level` argument mirrors `mpgo_encrypt_file`
    (`DATASET_GROUP`/`DATASET`/`DESCRIPTOR_STREAM`/`ACCESS_UNIT`).
  - Non-writable destinations (`http://`, `https://`, `file://`, or
    any unrecognised scheme) are rejected up front with the new
    `scheme_not_writable` error code; fsspec write failures surface
    as the new `upload_failed` code.
  - `mpgo_encrypt_file` / `mpgo_decrypt_file` remain local-only.
    DEPLOYMENT-GUIDE.md now documents the three-tier cloud workflow:
    `push_file` for fresh uploads, manual pull→encrypt→push for
    post-hoc encryption of existing cloud objects, and plain
    `register_file` for plaintext cloud reads.

### Tests
- 6 new tests in `tests/test_m6_push_file.py` (plaintext push,
  encrypted push with spectrum read-back, `https://`/`file://`
  rejection, missing local source, unknown `key_id`). Tests reuse
  the existing motoserver S3 fixture and skip unless the cloud extras
  are installed. Suite total: **74 tests** (was 68).
- `test_tools_surface_has_all_10` renamed to
  `test_tools_surface_has_all_11` and gains `mpgo_push_file`.

## [0.5.0.dev0] — 2026-04-24

### Added
- Encryption tools and server-side keyring (M5).
  - `mpeg_o_mcp.keyring` — JSON-file-backed keyring at
    `MPGO_KEYRING_PATH`. Keys are stored as base64 values with an
    `AES-256-GCM` algorithm tag and optional `created_at` /
    `description` metadata. Raw key bytes never cross the MCP wire;
    tools reference keys by `key_id` and the server resolves them
    in-process. Missing files are treated as empty keyrings; invalid
    JSON, missing values, wrong algorithms, non-32-byte keys, or
    invalid base64 surface as structured `invalid_keyring` errors.
  - Two new MCP tools:
    - `mpgo_encrypt_file` — in-place AES-256-GCM intensity encryption
      via MPEG-O `SpectralDataset.encrypt_with_key`. Takes `key_id`
      and optional `level` (`DATASET_GROUP` default, plus `DATASET`,
      `DESCRIPTOR_STREAM`, `ACCESS_UNIT`). Rehashes on-disk bytes and
      updates `files.encrypted` / `files.encrypted_algorithm` /
      `file_sha256` / `content_sha256` / `last_verified_at`. Local
      files only.
    - `mpgo_decrypt_file` — persist plaintext back to disk via the
      MPEG-O v1.1.1 `SpectralDataset.decrypt_in_place` API. Mirrors
      the encrypt catalog bookkeeping (clears `encrypted_algorithm`,
      rehashes). Local files only.
  - `mpgo_get_spectrum` gains a `key_id` parameter. Reading an
    encrypted file without `key_id` raises the new `key_required`
    error; with a key, the tool rehydrates plaintext in memory via
    `decrypt_with_key` — the disk bytes are not touched.
  - Alembic migration `65fda2fc1cfe_encrypted_algorithm` adds a
    nullable `files.encrypted_algorithm` column.
  - `as_user` is now validated: unknown names raise the new
    `unknown_user` error instead of auto-creating a row. The seeded
    `system` user remains the default.
  - Runtime dependency bumped to `mpeg-o @ v1.1.1` for
    `SpectralDataset.decrypt_in_place`.
  - 25 new tests across three suites:
    - `tests/test_m5_keyring.py` — file-format validation, base64 /
      algorithm / length errors, `reload()`, env/path constructors.
    - `tests/test_m5_encrypt_decrypt.py` — encrypt → reverify →
      get_spectrum → decrypt round-trip, duplicate encrypt rejected,
      decrypt-without-encrypt rejected, remote URIs rejected, wrong
      key surfaces `read_failed`.
    - `tests/test_m5_as_user.py` — unknown name raises, no side-effect
      row creation, existing user accepted.

## [0.4.0.dev0] — 2026-04-23

### Added
- Cloud URI reads (M4).
  - `mpeg_o_mcp.catalog.resolve_uri` and a new `ResolvedTarget`
    dataclass replace the local-only `resolve_local_path` on every
    caller path (tool lookups, hashing, `SpectralDataset.open`).
    `resolve_local_path` is retained for back-compat but is no longer
    called outside catalog-internal code.
  - Whole-object streaming hash for remote URIs via
    `mpeg_o.remote.open_remote_file`; remote and local hashing
    agree byte-for-byte.
  - `MPGO_MCP_FSSPEC_KWARGS` (JSON object) provides default
    `fsspec.open` kwargs for every cloud call. Per-call
    `fsspec_kwargs` on `mpgo_register_file` and `mpgo_get_spectrum`
    shallow-merge on top, with per-call keys winning.
  - Supported schemes now include `s3://`, `https://`, `http://`,
    `gs://`, `gcs://`, `abfs://`, `abfss://`, `az://` — anything
    `mpeg_o.remote.is_remote_url` recognises.
  - Six new tests against a `ThreadedMotoServer` S3 endpoint
    (registration, hash parity with local, catalog lookup survives,
    lazy spectrum read, env default, per-call override). The suite
    skips cleanly if `moto[server,s3]`, `flask`, or `s3fs` are
    missing.
  - Keyring / encryption / real per-user auth remain deferred to M5.

## [0.3.0.dev0] — 2026-04-23

### Added
- Query, spectra, and quantifications (M3).
  - `quantifications` table via Alembic migration on top of
    `d556e849b813`; composite index `ix_quantifications_chebi_sample`
    for hot `(chebi_id, sample_ref)` lookups.
  - `mpeg_o_mcp.catalog.register_file` now eagerly materialises
    `ds.quantifications()` and replaces them atomically on
    re-registration.
  - Four new MCP tools: `mpgo_search_identifications`,
    `mpgo_get_run`, `mpgo_get_spectrum`, `mpgo_get_quantifications`.
  - `mpgo_get_spectrum` is the only query tool that reopens the
    underlying `.mpgo` — all others answer from the catalog. Channels
    past `max_points` (default 1000, cap 100000) are stride-downsampled
    and flagged with `truncated: true`.
  - 15 new tests (6 catalog/query, 4 spectrum read incl. NMR and
    downsampling, 3 quantification filters, tool surface sanity).
  - Tool-level error codes gain `invalid_argument` and `read_failed`.

## [0.2.0.dev0] — 2026-04-23

### Added
- Catalog and file registration (M2).
  - `mpeg_o_mcp.hashes` — streaming `file_sha256`. `content_sha256` is
    an alias in M2; semantic-content hashing deferred.
  - `mpeg_o_mcp.catalog.register_file` — atomic URI → hash → open →
    extract → upsert into `files` / `studies` / `runs` /
    `identifications` / `provenance_records`. Idempotent on `uri`.
  - Four MCP tools, one per concept: `mpgo_register_file`,
    `mpgo_list_files`, `mpgo_get_file`, `mpgo_reverify`.
  - Server wires in a SQLAlchemy session factory and runs sync MPEG-O
    I/O under `asyncio.to_thread`.
  - 18 new tests: catalog round-trip for MS and NMR fixtures,
    idempotent re-registration, filters + pagination, drift detection
    on reverify.
  - Tool-level error model (`CatalogError` subclasses → structured
    `{ok: false, error: {code, message}}` responses).
- Local URIs only in M2 (`file://` and bare paths); `s3://` / `https://`
  deferred to M4.

## [0.1.0.dev0] — 2026-04-23

### Added
- Initial scaffolding (M1).
  - `mpeg-o-mcp` Python package with hatchling build backend.
  - SQLAlchemy 2.x declarative schema: `users`, `files`, `studies`, `runs`,
    `identifications`, `provenance_records` (SQLite v0.1, Postgres-portable).
  - Alembic baseline migration seeding a `system` user.
  - MCP server stub (`mpeg_o_mcp.server:main`) that answers the
    `initialize` handshake over stdio; zero tools registered in M1.
  - Tests for schema + initialize handshake.
  - GitHub Actions CI matrix on Python 3.11 and 3.12.
  - Runtime dependency on `mpeg-o` pulled from the v1.0.0 git tag
    (PyPI publish tracked as MPEG-O M40).
