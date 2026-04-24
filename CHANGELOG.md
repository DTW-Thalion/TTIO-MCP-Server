# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
