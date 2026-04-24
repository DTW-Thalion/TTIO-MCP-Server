# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
