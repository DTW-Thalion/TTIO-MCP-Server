# TTIO Workbench MCP Server — Design

**Date:** 2026-06-10
**Status:** Approved (design); implementation plan to follow
**Repo:** `DTW-Thalion/TTIO-MCP-Server` (greenfield rewrite in-repo)

## 1. Purpose

Replace the current TTIO-MCP-Server (which talks to local `.mpgo` files via the
legacy `mpeg-o @ v1.1.1` dependency — a package that has **no workbench client at
all**) with an MCP server that is a **client of `tti-workbench-server`**.

The new server exposes, as MCP tools, all of the **non-admin** functionality that
the `tio-browser` desktop client provides: authentication, browsing/querying
server data, jobs/pipelines, interactive sessions, encrypted/plain transfers, and
reading/extracting `.tio` dataset contents — driven by the `ttio.workbench.*`
Python SDK and `ttio.SpectralDataset`.

### Why the existing codebase is invalid for this goal

The existing server pins `mpeg-o @ v1.1.1` and serves local `.mpgo` files. That
package predates the workbench SDK: none of `connect`, `WorkbenchClient`,
`download_via_server`, `upload_encrypted_multi`, `ServerRecipient`,
`wrap_for_server`/`unwrap_for_server` exist in it. The new server depends on
**`ttio >= 1.7` (`[network,crypto]` extras; `pqc` for ML-KEM)** and drops the
`mpeg-o` pin entirely.

## 2. System context

Three systems, verified against source:

- **`tti-workbench-server`** — ObjC/GNUstep WebSocket+HTTP daemon. Default port
  `18443`. HTTP/1.1 REST + SSE control plane (`/v1/...`), WebSocket data plane
  (`/transport`, subprotocol `ttio-transport`). DB-backed bearer tokens
  (`ttiowbs_…` session, `ttiowbk_…` API key); HTTP uses `Authorization: Bearer`,
  WS carries the token inside the first handshake JSON frame.
- **`tio-browser`** — JavaFX desktop client. Has **no admin UI**; every feature
  routes through the Java `global.thalion.ttio.workbench` package, whose Python
  sibling is `ttio.workbench.*`. This is the functional spec for "what to replicate."
- **`ttio` Python library (v1.7.1)** — provides both the workbench client SDK
  (`ttio.workbench.*`) and the at-rest reader (`ttio.SpectralDataset`). Import
  name `ttio`. The MCP server is a thin, well-tested wrapper over this.

## 3. Scope

### In scope (non-admin)

| Area | Server surface | `ttio` API |
|---|---|---|
| Auth | `POST /v1/auth/login`, `/logout`, `/whoami` | `ttio.connect`, `PasswordTotpAuth`, `BearerAuth`, `Session` |
| Containers (browse) | `GET /v1/containers[/{uri}[/layers\|/manifest]]` | `client.containers()` |
| Cohorts | `POST /v1/cohorts/query`, `/preview-count` | `client.query()`, `client.preview_count()` |
| Jobs | `POST/GET/DELETE /v1/jobs`, `GET /v1/jobs/{id}/events` (SSE) | `client.jobs()`, `submit_pipeline()` |
| Pipelines (read) | `GET /v1/pipelines[/{id}]` | `client.pipelines()` |
| Sessions | `POST/GET/DELETE /v1/sessions`, attach WS URL | `client.sessions()`, `session_proxy()` |
| Transfers | WS `/transport`; key-custody `wrap/unwrap-for-server` | `upload_*` / `download_*` |
| Federation | `GET /v1/federation/peers` | `client.federation()` |
| Data reading | — (local `.tio`) | `ttio.SpectralDataset.open()` |

Transfer modes: **plain**, **BYOK** (caller key, AES-256-GCM per-AU),
**server-KEK** (`upload_encrypted_multi` + `ServerRecipient` → `download_via_server`,
server unwraps DEK via HSM, client decrypts at the edge), **PQC** (ML-KEM-1024,
preview-gated).

### Out of scope (excluded)

- **All admin:** user management (`/v1/auth/users`), groups (`/v1/auth/groups`),
  operations dashboard (`/dash/api/*`), KEK rotation/rewrap CLIs, pipeline
  **registration** (`POST /v1/pipelines`, `pipelines.manage`). None of these exist
  in tio-browser.
- **Container delete** (`DELETE /v1/containers/{uri}`) — destructive; explicitly
  excluded. The server exposes **no destructive operations**. (Upload/job-submit
  are creative writes and remain in scope.)
- **Self-service API-key management tools** — not in tio-browser. The server still
  *consumes* a configured API key for auth, but does not create/revoke keys.
- **Interactive TTY attach** — the server surfaces the WS attach URL string; it
  does not embed a terminal.

## 4. Decisions (from brainstorming)

1. **Codebase:** greenfield rewrite in-repo — keep git history, packaging, CI;
   swap dependency to `ttio>=1.7`; delete `.mpgo`-file logic.
2. **Auth model:** `ttio_login` tool (url+username+password+TOTP) for interactive
   sessions **plus** long-lived API key / bearer token via env/config for headless
   use. Single in-memory session; tokens never persisted to disk.
3. **Data return:** compact summaries by default (counts, ranges, top-N peaks,
   downsampled previews, metadata); a separate `export` action writes full arrays
   to a local file (Parquet/CSV/JSON, optional PNG plot) and returns the path.
4. **Optional scope included:** server-KEK encrypted transfers, interactive
   sessions, PQC + federation. **Container delete excluded.**
5. **Tool consolidation:** single `ttio_upload`/`ttio_download` with a `mode`
   enum, single `ttio_dataset_read` with a `what` selector — target ~20 tools.
6. **Package name:** keep `ttio_mcp`.

## 5. Architecture

Approach **A**: a thin MCP layer over the `ttio` SDK with a stateful connection
manager. (Rejected: B stateless pass-through — conflicts with the login-tool
model; C auto-generated passthrough — huge undifferentiated tool list, leaks
async/raw types, no summarization.)

```
src/ttio_mcp/
  server.py        # FastMCP app + entrypoint; registers all tool modules
  config.py        # env/config: TTIO_WB_URL, optional API key/bearer token,
                   #   export/cache dirs, default page sizes
  connection.py    # ConnectionManager singleton: holds WorkbenchClient + Session;
                   #   login / reauth / expiry detection / status; single connection
  errors.py        # map ttio errors → clean, actionable MCP messages
  summarize.py     # downsampling, top-N peak picking, stats, previews
  export.py        # write arrays → Parquet/CSV/JSON (+ optional PNG plot); return path
  tools/
    auth.py        # ttio_login, ttio_whoami, ttio_logout, ttio_connection_status
    containers.py  # list, get, layers, manifest
    cohorts.py     # query, preview_count (+ predicate-builder helper)
    jobs.py        # submit, list, get, cancel, events; pipelines_list, pipelines_get
    sessions.py    # create, list, get, terminate, attach_url
    transfers.py   # upload, download (mode: plain|byok|server-kek|pqc); federation_peers
    data.py        # dataset_summary, read (what selector), export
tests/             # unit (mocked WorkbenchClient) + opt-in live-daemon integration
```

### Components

- **`ConnectionManager`** — owns the single `WorkbenchClient` + `Session`.
  `login(...)`, `connect_from_config()`, `status()`, `require_client()` (raises a
  clean "not connected / session expired" error). Knows API-key sessions don't
  expire and password+TOTP sessions do (no silent reauth — TOTP rotates).
- **Tool modules** — each registers `@mcp.tool` async functions for one domain,
  delegating to `ConnectionManager.client`. Thin: validate args, call SDK, shape
  the response via `summarize`/`export`, map errors.
- **`summarize` / `export`** — the token-economy layer. Summaries inline; full
  fidelity on explicit export to a configured directory.
- **`errors`** — translate `InvalidCredentials` (401), `AccountDisabled` (423),
  `RateLimitExceeded` (429, honor `Retry-After`), `WorkbenchHttpError` (403 →
  "missing capability"), `Handshake/Upload/DownloadError` (WS close code + reason).

### Data flow

```
LLM → MCP tool → ConnectionManager.client → ttio.workbench client → workbench-server
                                          ↘ (download to local cache .tio)
data tools → ttio.SpectralDataset.open(cache.tio) → summarize → inline result
                                                   → export → derived file path
```

### Async model

All tool handlers are `async` (FastMCP supports async tools). WS transfer methods
(`upload_*`, `download_*`) are awaited directly; synchronous REST sub-client calls
(`containers()`, `jobs()`, `query()`, …) are wrapped in `asyncio.to_thread`. One
event loop, owned by the MCP runtime; the `WorkbenchClient` is created/held by the
`ConnectionManager`.

## 6. Tool inventory (target ~20)

**Auth:** `ttio_login`, `ttio_whoami`, `ttio_logout`, `ttio_connection_status`.
**Containers:** `ttio_containers_list`, `ttio_container_get`, `ttio_container_layers`, `ttio_container_manifest`.
**Cohorts:** `ttio_cohort_query`, `ttio_cohort_preview_count`.
**Jobs/pipelines:** `ttio_job_submit`, `ttio_jobs_list`, `ttio_job_get`, `ttio_job_cancel`, `ttio_job_events`, `ttio_pipelines_list`, `ttio_pipeline_get`.
**Sessions:** `ttio_session_create`, `ttio_sessions_list`, `ttio_session_get`, `ttio_session_terminate`, `ttio_session_attach_url`.
**Transfers:** `ttio_upload` (`mode`), `ttio_download` (`mode`), `ttio_federation_peers`.
**Data:** `ttio_dataset_summary`, `ttio_dataset_read` (`what`: runs|spectrum|signal|subjects|samples|images|identifications|quantifications|provenance), `ttio_dataset_export`.

(Counts ~28 named functions; consolidation keeps the *distinct* transfer/read
tools to a handful. Exact final grouping settled during implementation, but the
`mode`/`what` consolidation is fixed.)

## 7. Error handling

Every tool returns either a structured result or a clean error string. Mapping:
- Not connected / expired session → instruct to call `ttio_login` (or check config token).
- 401/423/429 → credential / disabled / rate-limit (with `Retry-After`).
- 403 → name the missing capability from the catalog when derivable.
- WS errors → close code + reason; resumable uploads surface the `resume_handle`.
- `.tio` decode errors → file path + underlying message; never leak raw tracebacks.

## 8. Testing strategy

- **TDD.** Each tool gets unit tests first, against a **mocked `WorkbenchClient`**
  (the SDK's own WS/REST drivers are excluded from coverage upstream because they
  need a live daemon, so we mirror that boundary).
- **Summarize/export** get pure-function unit tests with synthetic arrays.
- **Integration suite (opt-in):** drives the real flows against a locally launched
  `tti-workbench-server` (bootstrap-admin), gated by an env flag like the server
  repo's `smoke_*` scripts. Round-trip checks compare array **content**, not bytes
  (HDF5 is not byte-reproducible across encrypt/decrypt).
- Reuse the repo's existing pytest scaffolding and CI; CI runs unit tests, the
  live suite runs manually / in a dedicated job.

## 9. Migration / cleanup

- Drop `mpeg-o` dependency; add `ttio[network,crypto]` (+`pqc`).
- Remove `.mpgo`-file tools, intake/signature/S3-push logic that served the old
  purpose (audit each against the new scope before deletion).
- Update `docs/configuration.md`, `docs/tools.md`, README to the new model.
- Bump version; the rename history (`mpgo_* → ttio_*`, `MPGO_* → TTIO_*`) stays.

## 10. Open implementation details (resolved during planning, not blockers)

- Exact consolidation of transfer/read sub-actions into tool signatures.
- Whether `export` PNG plotting pulls in matplotlib (optional extra) or stays
  data-only (Parquet/CSV/JSON) initially.
- Cache directory lifecycle / cleanup policy for downloaded `.tio` files.
- Concrete summary shapes per spectrum/run/genomic modality.
