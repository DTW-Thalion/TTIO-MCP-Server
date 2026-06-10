# Configuration

All configuration is read from the environment of the process that
launches `ttio-mcp`. The server never accepts secrets through MCP
tool arguments; tokens are never persisted to disk.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TTIO_WB_URL` | *(unset)* | Workbench server URL, e.g. `https://wb.example.com:18443` or `wss://wb.example.com:18443/transport`. Required for auto-connect; may also be passed per-call to `ttio_login`. |
| `TTIO_WB_TOKEN` | *(unset)* | Long-lived API key (`ttiowbk_...`) or bearer token (`ttiowbs_...`) for headless auto-connect at startup. |
| `TTIO_WB_USERNAME` | *(unset)* | Optional username label attached to the session (informational). |
| `TTIO_MCP_EXPORT_DIR` | `~/.local/state/ttio-mcp/exports` | Directory where `ttio_dataset_export` writes parquet/csv/json output files. |
| `TTIO_MCP_CACHE_DIR` | `~/.local/state/ttio-mcp/cache` | Directory used for intermediate cache files. |
| `TTIO_MCP_PAGE_SIZE` | `100` | Default page size for container list calls when the caller does not pass `limit`. |

## Authentication

Two paths are supported. Tokens are held in memory for the lifetime
of the server process and are never written to disk.

### Interactive login

Leave `TTIO_WB_TOKEN` unset and call `ttio_login` from the LLM client
after the server starts:

```jsonc
// tool call
{
  "username": "alice",
  "password": "hunter2",
  "totp": "123456",
  "url": "https://wb.example.com:18443"   // optional; overrides TTIO_WB_URL
}
```

`url` defaults to `TTIO_WB_URL` when omitted. The session token
expires after approximately 24 hours; call `ttio_login` again to
refresh it. Call `ttio_logout` to drop the in-memory session. Neither
action touches disk.

### Headless / API-key auto-connect

Set both `TTIO_WB_URL` and `TTIO_WB_TOKEN` before launching `ttio-mcp`.
The server establishes a session at startup; no `ttio_login` call is
needed:

```bash
export TTIO_WB_URL="https://wb.example.com:18443"
export TTIO_WB_TOKEN="ttiowbk_abc123..."
ttio-mcp
```

API keys (`ttiowbk_...`) are issued by a workbench administrator from
the Operations Dashboard. They do not expire on their own but can be
revoked server-side. Bearer tokens (`ttiowbs_...`) are short-lived
session tokens obtained via a prior login and are less suitable for
unattended deployments.

## `TTIO_MCP_EXPORT_DIR`

Writable directory for `ttio_dataset_export` output. Parquet, CSV, and
JSON export files land here by default; callers may override per-call
with the `out_dir` parameter.

```bash
export TTIO_MCP_EXPORT_DIR="$HOME/ttio-exports"
```

The directory is created on first use if it does not exist.

## `TTIO_MCP_CACHE_DIR`

Writable directory for intermediate cache files. Separate from the
export directory so caches can be cleared without touching exported
results.

```bash
export TTIO_MCP_CACHE_DIR="/var/cache/ttio-mcp"
```

## `TTIO_MCP_PAGE_SIZE`

Integer. Controls the default `limit` passed to container-list calls
when the caller does not supply one.

```bash
export TTIO_MCP_PAGE_SIZE=50
```

## Transport

stdio only. Configure the server in whatever launches `ttio-mcp`:

```bash
claude mcp add ttio-mcp -- ttio-mcp
```

The server name (`ttio-mcp`) and version (from `ttio_mcp.__version__`)
are reported in the MCP `initialize` response.
MCP-over-HTTP and SSE are not implemented.
