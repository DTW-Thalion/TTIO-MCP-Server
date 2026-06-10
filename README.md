# TTI-O MCP Server

An [MCP](https://modelcontextprotocol.io/) server that gives LLM clients
non-admin access to a running
[tti-workbench-server](https://github.com/DTW-Thalion/tti-workbench-server)
instance. The server acts as a regular workbench client — it holds a
session token in memory, calls the workbench REST/WebSocket API on behalf
of the LLM, and never requires local `.tio` files to use the main
container/cohort/job/session tools.

## What it exposes

28 tools across seven domains:

| Domain | Tools |
|---|---|
| **Auth** | `ttio_login`, `ttio_whoami`, `ttio_logout`, `ttio_connection_status` |
| **Containers** | `ttio_containers_list`, `ttio_container_get`, `ttio_container_layers`, `ttio_container_manifest` |
| **Cohorts** | `ttio_cohort_query`, `ttio_cohort_preview_count` |
| **Jobs / Pipelines** | `ttio_job_submit`, `ttio_jobs_list`, `ttio_job_get`, `ttio_job_cancel`, `ttio_job_events`, `ttio_pipelines_list`, `ttio_pipeline_get` |
| **Sessions** | `ttio_session_create`, `ttio_sessions_list`, `ttio_session_get`, `ttio_session_terminate`, `ttio_session_attach_url` |
| **Transfers** | `ttio_upload`, `ttio_download`, `ttio_federation_peers` |
| **Data** | `ttio_dataset_summary`, `ttio_dataset_read`, `ttio_dataset_export` |

Transfer encryption modes: `plain`, `byok` (caller-key AES-256-GCM),
`server-kek` (HSM-wrapped DEK), `pqc` (ML-KEM-1024, preview-gated).

Data tools read a **local** `.tio` file (e.g. one fetched via
`ttio_download`). Summaries are returned inline; full signal arrays use
`ttio_dataset_export` (parquet / csv / json).

Admin capabilities (user management, groups, operations dashboard, KEK
rotation, pipeline registration) and container delete are intentionally
not exposed.

## Requirements

- Python 3.11 or 3.12
- A reachable `tti-workbench-server` instance (v1.1.0 or later)

## Install

`ttio-mcp` ships from this repository (it is not published to PyPI). Install the
pinned **v0.9.0** release with pip:

```bash
pip install "ttio-mcp @ git+https://github.com/DTW-Thalion/TTIO-MCP-Server.git@v0.9.0"
```

This installs the `ttio-mcp` console script and pulls its pinned
`ttio[network,crypto]` dependency. To enable the optional transfer extras —
post-quantum (`pqc`, ML-KEM-1024) and remote-`.tio` URLs (`cloud`) — request
them by name:

```bash
pip install "ttio-mcp[pqc] @ git+https://github.com/DTW-Thalion/TTIO-MCP-Server.git@v0.9.0"
```

> Installing builds the `ttio` SDK from source, so `git` and a C toolchain
> (e.g. `build-essential` on Debian/Ubuntu, the Xcode Command Line Tools on
> macOS) must be available.

### From a clone (development)

```bash
git clone https://github.com/DTW-Thalion/TTIO-MCP-Server.git
cd TTIO-MCP-Server
git checkout v0.9.0
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Quickstart

### 1. Configure

```bash
# Required: URL of your workbench server
export TTIO_WB_URL="https://wb.example.com:18443"

# Optional: API key for headless auto-connect (no login call needed)
export TTIO_WB_TOKEN="ttiowbk_abc123..."
```

### 2. Run

```bash
ttio-mcp
```

Wire up with Claude Code:

```bash
claude mcp add ttio-mcp -- ttio-mcp
```

### 3. Connect

**Interactive** — call `ttio_login` from the LLM after the server starts:

```jsonc
// tool call: ttio_login
{"username": "alice", "password": "...", "totp": "123456"}
```

**Headless** — if `TTIO_WB_URL` and `TTIO_WB_TOKEN` are both set, the
server auto-connects at startup and no login call is needed.

Check status at any time with `ttio_connection_status` or `ttio_whoami`.

## Configuration reference

| Env var | Default | Purpose |
|---|---|---|
| `TTIO_WB_URL` | *(required)* | Workbench server URL. |
| `TTIO_WB_TOKEN` | *(unset)* | API key or bearer token for headless auto-connect. |
| `TTIO_WB_USERNAME` | *(unset)* | Optional username label (informational). |
| `TTIO_MCP_EXPORT_DIR` | `~/.local/state/ttio-mcp/exports` | Directory for `ttio_dataset_export` output. |
| `TTIO_MCP_CACHE_DIR` | `~/.local/state/ttio-mcp/cache` | Directory for intermediate cache files. |
| `TTIO_MCP_PAGE_SIZE` | `100` | Default container list page size. |

Details: [docs/configuration.md](docs/configuration.md).

Full tool catalog: [docs/tools.md](docs/tools.md).

## Development

```bash
pytest -q        # 55 passed, 12 skipped expected
ruff check src tests
```

CI runs the same commands across Python 3.11 and 3.12 on Ubuntu
(`.github/workflows/ci.yml`). The 12 skipped tests are the opt-in live
integration suite — enable them with `TTIO_MCP_LIVE=1` against a running
workbench server (see `tests/integration/test_live_smoke.py`).

## License

Apache-2.0 — see [LICENSE](LICENSE).
