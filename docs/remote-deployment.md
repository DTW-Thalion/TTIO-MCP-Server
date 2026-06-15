# Remote deployment (streamable-HTTP)

`ttio-mcp` can run over **streamable-HTTP** instead of stdio, so one deployed
instance can be added to Claude as a **custom connector by URL** (no local
install). This is **Phase 0** of the remote-connector work (see
[remote-connector-scope.md](remote-connector-scope.md)).

> **Phase-0 limitation — single shared session.** This server still holds ONE
> in-memory workbench session (the service account from `TTIO_WB_URL` +
> `TTIO_WB_TOKEN`). **Every connected user shares that one identity.** There is
> no per-user authentication or isolation yet — that is Phase 1 (per-session
> tenancy) and Phase 2 (per-user OAuth). Deploy this only for a single trusted
> team/service account until those land.

## Configuration

| Env var | Purpose | Example |
|---|---|---|
| `TTIO_MCP_TRANSPORT` | `stdio` (default) or `http` | `http` |
| `TTIO_MCP_HTTP_HOST` | Bind address | `0.0.0.0` |
| `TTIO_MCP_HTTP_PORT` | Bind port | `8000` |
| `TTIO_MCP_HTTP_PATH` | MCP endpoint path | `/mcp` (default) |
| `TTIO_WB_URL` | Workbench server URL | `https://wb.example.com:18443` |
| `TTIO_WB_TOKEN` | **Service-account** API key (`ttiowbk_…`) | `ttiowbk_…` |

The MCP endpoint is served at `http://<host>:<port><TTIO_MCP_HTTP_PATH>` (default
`/mcp`); a `GET /healthz` route returns `{"status":"ok"}` for liveness checks.

## Run with Docker

```bash
docker build -t ttio-mcp:remote .
docker run --rm -p 8000:8000 \
  -e TTIO_WB_URL="https://wb.example.com:18443" \
  -e TTIO_WB_TOKEN="ttiowbk_…" \
  ttio-mcp:remote

# Liveness:
curl http://localhost:8000/healthz        # {"status":"ok"}
```

## Run without Docker

```bash
pip install ".[http]"
TTIO_MCP_TRANSPORT=http TTIO_MCP_HTTP_HOST=0.0.0.0 TTIO_MCP_HTTP_PORT=8000 \
TTIO_WB_URL="https://wb.example.com:18443" TTIO_WB_TOKEN="ttiowbk_…" \
ttio-mcp
```

## TLS and the public URL

Claude requires connectors over **HTTPS**. Terminate TLS at a reverse proxy
(nginx/Caddy/Traefik) or your platform's load balancer and forward to the
container's `:8000`. The public connector URL is then:

```
https://<your-host>/mcp
```

Point the proxy health check at `/healthz`.

## Add it to Claude (custom connector by URL)

In Claude (Pro / Team / Enterprise) → Settings → Connectors → **Add custom
connector**, paste `https://<your-host>/mcp`. The 28 `ttio_*` tools become
available. (Listing in Claude's *curated* connector directory is a separate
Anthropic partnership process — see the scope doc.)

## Verify end to end

With the server running, drive it with a real MCP client (the repo's opt-in
smoke does exactly this):

```bash
TTIO_MCP_HTTP_SMOKE=1 .venv/bin/python -m pytest tests/integration/test_http_smoke.py
```

It starts the server over HTTP, polls `/healthz`, then `initialize`s and lists
all 28 tools through `streamablehttp_client`.

## OAuth resource-server mode (Phase 2 SP3)

Set `TTIO_MCP_OAUTH_ISSUER` to turn the server into a **per-user OAuth resource
server**. Each MCP client presents its own Keycloak access token; the server
validates it and issues a per-session workbench client. No shared service-account
token is needed.

| Env var | Required | Purpose |
|---|---|---|
| `TTIO_MCP_OAUTH_ISSUER` | yes | Keycloak realm URL (enables OAuth mode). |
| `TTIO_MCP_OAUTH_RESOURCE_URL` | yes | Public URL of this server's `/mcp` endpoint. |
| `TTIO_MCP_OAUTH_JWKS_URL` | yes | Keycloak JWKS endpoint. |
| `TTIO_MCP_OAUTH_TOKEN_URL` | yes | Keycloak token endpoint (RFC 8693 exchange). |
| `TTIO_MCP_OAUTH_CLIENT_ID` | yes | Client ID for `ttio-mcp` in Keycloak. |
| `TTIO_MCP_OAUTH_CLIENT_SECRET` | yes | Client secret. |
| `TTIO_MCP_OAUTH_AUDIENCE` | no | `aud` claim to expect (default `ttio-mcp`). |
| `TTIO_MCP_OAUTH_EXCHANGE_AUDIENCE` | no | Audience for the exchanged workbench token (default `tti-workbench`). |

**Run with Docker (OAuth mode):**

```bash
docker run --rm -p 8000:8000 \
  -e TTIO_MCP_TRANSPORT=http \
  -e TTIO_WB_URL="https://wb.example.com:18443" \
  -e TTIO_MCP_OAUTH_ISSUER="https://kc.example.com/realms/ttio" \
  -e TTIO_MCP_OAUTH_RESOURCE_URL="https://mcp.example.com/mcp" \
  -e TTIO_MCP_OAUTH_JWKS_URL="https://kc.example.com/realms/ttio/protocol/openid-connect/certs" \
  -e TTIO_MCP_OAUTH_TOKEN_URL="https://kc.example.com/realms/ttio/protocol/openid-connect/token" \
  -e TTIO_MCP_OAUTH_CLIENT_ID="ttio-mcp" \
  -e TTIO_MCP_OAUTH_CLIENT_SECRET="..." \
  ttio-mcp:remote
```

**Verify OAuth endpoints** (server running, no valid token):

```bash
# Protected-resource metadata (RFC 9728)
curl https://mcp.example.com/.well-known/oauth-protected-resource/mcp

# Unauthenticated POST → 401 + WWW-Authenticate: Bearer
curl -s -o /dev/null -w "%{http_code}" -X POST https://mcp.example.com/mcp
```

**Run the opt-in OAuth smoke test** (requires a live Keycloak):

```bash
TTIO_MCP_OAUTH_SMOKE=1 \
TTIO_MCP_OAUTH_ISSUER="https://kc.example.com/realms/ttio" \
TTIO_MCP_OAUTH_RESOURCE_URL="https://127.0.0.1:8000/mcp" \
TTIO_MCP_OAUTH_JWKS_URL="https://kc.example.com/realms/ttio/protocol/openid-connect/certs" \
TTIO_MCP_OAUTH_TOKEN_URL="https://kc.example.com/realms/ttio/protocol/openid-connect/token" \
TTIO_MCP_OAUTH_CLIENT_ID="ttio-mcp" \
TTIO_MCP_OAUTH_CLIENT_SECRET="..." \
TTIO_MCP_OAUTH_USER_TOKEN="<user-access-token>" \
.venv/bin/python -m pytest tests/integration/test_http_oauth_smoke.py -v
```

See [docs/configuration.md](configuration.md) for the full OAuth variable reference.

## Operational notes

- The service-account token lives only in process memory; it is never written to
  disk. Scope the account to least privilege and rotate the key.
- A public endpoint is internet-reachable — put it behind the proxy with TLS and
  consider rate limiting (hardening is Phase 3).
- Stateless restarts are safe; there is no local database or catalog.
