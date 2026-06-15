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

## Operational notes

- The service-account token lives only in process memory; it is never written to
  disk. Scope the account to least privilege and rotate the key.
- A public endpoint is internet-reachable — put it behind the proxy with TLS and
  consider rate limiting (hardening is Phase 3).
- Stateless restarts are safe; there is no local database or catalog.
