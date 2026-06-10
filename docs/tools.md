# Tool reference

The server registers 28 MCP tools. Tools are grouped below by domain.
All responses are plain dicts (FastMCP serialises them as JSON text
content). Errors surface as a dict with an `"error"` key; successful
responses carry tool-specific keys.

---

## Auth

| Tool | Parameters | Notes |
|---|---|---|
| `ttio_login` | `username`, `password`, `totp` (required); `url` (optional) | Log in with username + password + current 6-digit TOTP. `url` overrides `TTIO_WB_URL`. Returns session identity dict. |
| `ttio_whoami` | — | Return the current session identity (username, projects, capabilities). |
| `ttio_logout` | — | Drop the in-memory session (client-side only; tokens are not persisted to disk). |
| `ttio_connection_status` | — | Report current workbench connection state. |

---

## Containers

All container tools require an active session.

| Tool | Parameters | Notes |
|---|---|---|
| `ttio_containers_list` | `project`, `owner` (optional filters); `limit`, `cursor` | Paginated container listing. Returns `containers[]`, `next_cursor`, `has_more`. |
| `ttio_container_get` | `uri` (required) | Get one container's detail row and file stats by URI. |
| `ttio_container_layers` | `uri` (required) | List a container's auxiliary layers. |
| `ttio_container_manifest` | `uri` (required) | Get a container's HDF5 manifest projection (runs, counts, ISA ids). |

Container delete is intentionally not exposed — see "Not exposed" below.

---

## Cohorts

| Tool | Parameters | Notes |
|---|---|---|
| `ttio_cohort_query` | `select` (`containers`\|`subjects`\|`samples`); `predicate` (JSON tree); `order_by`, `limit`, `cursor` | Run a cohort query. Predicate leaves use `container_field` / `subject_field` / `sample_field` / `phenotype` plus `op` and `value`; composites use `{"op":"and"\|"or","children":[...]}` or `{"op":"not","child":...}`. Returns `rows[]`, `count`, `next_cursor`. |
| `ttio_cohort_preview_count` | `select`, `predicate` | Return the row count a cohort query would yield, without fetching rows. |

---

## Jobs / Pipelines

| Tool | Parameters | Notes |
|---|---|---|
| `ttio_job_submit` | `pipeline_id`, `inputs` (slot→uri map); `params` (optional) | Submit a pipeline job. A slot value of `{"cohort_query": <query-json>}` is auto-wrapped as a cohort input. |
| `ttio_jobs_list` | `status` (optional filter); `limit` | List jobs in the caller's project scope. |
| `ttio_job_get` | `job_id` | Get a single job row by id. |
| `ttio_job_cancel` | `job_id` | Cancel a job you own. |
| `ttio_job_events` | `job_id`; `max_events` (default 20) | Tail a job's live event stream (SSE); returns up to `max_events` then stops. |
| `ttio_pipelines_list` | — | List pipelines visible to the caller's project scope. |
| `ttio_pipeline_get` | `pipeline_id` | Get a single pipeline definition by id. |

Pipeline registration is intentionally not exposed — see "Not exposed" below.

---

## Sessions

| Tool | Parameters | Notes |
|---|---|---|
| `ttio_session_create` | `project`, `engine_pin` (required); `image`, `command`, `env`, `bind_mounts` (optional) | Start an interactive container session. `engine_pin` = `shell`\|`apptainer`\|`podman`\|... |
| `ttio_sessions_list` | `status` (optional filter); `limit` | List sessions in the caller's project scope. |
| `ttio_session_get` | `session_id` | Get a single session row by id. |
| `ttio_session_terminate` | `session_id` | Terminate a session you own. |
| `ttio_session_attach_url` | `session_id`; `path` (default `/`) | Return the WS attach URL for a running session. Connect with your own WebSocket client; no embedded TTY. |

---

## Transfers

Transfer mode selects the encryption strategy:

| Mode | Description |
|---|---|
| `plain` | No encryption; raw `.tis` bytes for download, plain `.tio` for upload. |
| `byok` | Caller-supplied AES-256-GCM key (hex 64 chars or base64, 32 bytes). Per-AU encryption. |
| `server-kek` | Multi-recipient: server wraps/unwraps DEK via HSM-held KEK. Pass `kek_id` on upload; server unwraps on download via `download_via_server`. |
| `pqc` | ML-KEM-1024 post-quantum encryption. Preview-gated; pass `preview=true`. |

| Tool | Parameters | Notes |
|---|---|---|
| `ttio_upload` | `project`, `container_uri`, `path` (required); `mode` (default `plain`); `key`, `kek_id`, `recipient_public_key`, `encrypt_headers`, `preview` | Upload a local `.tio` to the server using the selected mode. |
| `ttio_download` | `container_uri`, `out_path` (required); `mode` (default `plain`); `key`, `recipient_private_key`, `filters`, `max_au`, `preview` | Download a container to a local file. `filters` enables selective access (ms_level, polarity, retention time/precursor mz/charge). |
| `ttio_federation_peers` | — | List federation peers (empty on single-node v1.0). |

---

## Data

Data tools operate on a **local `.tio` file** (e.g. one fetched by
`ttio_download`). They do not require an active workbench session.

| Tool | Parameters | Notes |
|---|---|---|
| `ttio_dataset_summary` | `path` | Summarize a local `.tio`: title, encryption flag, runs with spectrum counts, subject/sample counts. |
| `ttio_dataset_read` | `path`, `what` (required); `run`, `index`, `signal`, `max_points`, `top_n`, `limit` | Read part of a local `.tio`. `what` = `runs`\|`spectrum`\|`signal`\|`subjects`\|`samples`\|`images`\|`identifications`\|`quantifications`\|`provenance`. Returns compact summaries; use `ttio_dataset_export` for full arrays. |
| `ttio_dataset_export` | `path`, `run` (required); `index`, `out_dir`, `basename`, `fmt` (default `parquet`) | Export a spectrum's full signal-channel arrays to a file. `fmt` = `parquet`\|`csv`\|`json`. Output lands in `TTIO_MCP_EXPORT_DIR` unless `out_dir` is supplied. |

---

## Not exposed (admin / destructive)

The following workbench capabilities are intentionally absent from the
MCP tool surface. They require elevated permissions and are not
appropriate for a non-admin client:

- **User management** — create/update/delete users, password resets.
- **Groups** — group create/update/delete, membership management.
- **Operations dashboard** — server health, metrics, audit log.
- **KEK rotation** — HSM key management via `TtioWBKmsRewrap`.
- **Pipeline registration** — registering new pipeline definitions.
- **Container delete** — no destructive data operations are exposed.
