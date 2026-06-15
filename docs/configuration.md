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

| Variable | Default | Purpose |
|---|---|---|
| `TTIO_MCP_TRANSPORT` | `stdio` | `stdio` (default) or `http` (streamable-HTTP). |
| `TTIO_MCP_HTTP_HOST` | `127.0.0.1` | Bind address when `TTIO_MCP_TRANSPORT=http`. |
| `TTIO_MCP_HTTP_PORT` | `8000` | Bind port when `TTIO_MCP_TRANSPORT=http`. |
| `TTIO_MCP_HTTP_PATH` | `/mcp` | MCP endpoint path when `TTIO_MCP_TRANSPORT=http`. |

For stdio, configure the server in whatever launches `ttio-mcp`:

```bash
claude mcp add ttio-mcp -- ttio-mcp
```

The server name (`ttio-mcp`) and version (from `ttio_mcp.__version__`)
are reported in the MCP `initialize` response.

## OAuth resource-server mode (HTTP transport only)

Setting `TTIO_MCP_OAUTH_ISSUER` switches the server into **OAuth resource-server
mode**. In this mode the server:

1. Validates every inbound bearer token against the Keycloak realm JWKS
   (audience `ttio-mcp`, RS256/ES256, scope `ttio.connector`).
2. Serves the RFC 9728 protected-resource metadata document at
   `GET /.well-known/oauth-protected-resource/mcp`.
3. Returns `401 Unauthorized` + `WWW-Authenticate: Bearer` for requests
   with a missing or invalid token.
4. Performs an RFC 8693 token exchange at Keycloak to obtain a
   `tti-workbench`-audience token, then builds a per-session workbench
   client from that token.

This mode requires `TTIO_MCP_TRANSPORT=http`.

| Variable | Default | Purpose |
|---|---|---|
| `TTIO_MCP_OAUTH_ISSUER` | *(unset)* | Keycloak realm URL, e.g. `https://kc.example.com/realms/ttio`. Setting this enables OAuth resource-server mode. |
| `TTIO_MCP_OAUTH_RESOURCE_URL` | *(unset)* | Public URL of this MCP server's `/mcp` endpoint (reported in protected-resource metadata), e.g. `https://mcp.example.com/mcp`. |
| `TTIO_MCP_OAUTH_JWKS_URL` | *(unset)* | Keycloak JWKS endpoint, e.g. `https://kc.example.com/realms/ttio/protocol/openid-connect/certs`. |
| `TTIO_MCP_OAUTH_TOKEN_URL` | *(unset)* | Keycloak token endpoint for RFC 8693 exchange, e.g. `https://kc.example.com/realms/ttio/protocol/openid-connect/token`. |
| `TTIO_MCP_OAUTH_CLIENT_ID` | *(unset)* | Client ID registered in Keycloak for `ttio-mcp`. |
| `TTIO_MCP_OAUTH_CLIENT_SECRET` | *(unset)* | Client secret for the above client. |
| `TTIO_MCP_OAUTH_AUDIENCE` | `ttio-mcp` | Expected `aud` claim in the inbound user token. |
| `TTIO_MCP_OAUTH_EXCHANGE_AUDIENCE` | `tti-workbench` | Target `audience` in the RFC 8693 token-exchange request; the exchanged token is used to connect to the workbench. |

### Token flow

```
MCP client  →  ttio-mcp (validates aud=ttio-mcp, scope=ttio.connector)
                    ↓  RFC 8693 exchange (Keycloak)
                    ↓  aud=tti-workbench token
               per-session ttio.workbench client
```

Users obtain a `ttio-mcp`-audience access token from the Keycloak realm
(scope `ttio.connector`). The MCP server validates that token on each
request, exchanges it for a `tti-workbench`-audience token, and builds
an isolated workbench client for that session. No headless `TTIO_WB_TOKEN`
is needed in OAuth mode.
