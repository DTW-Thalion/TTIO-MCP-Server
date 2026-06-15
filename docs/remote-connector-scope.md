# Scope: `ttio-mcp` as a one-click remote claude.ai connector

**Status:** scoped; key decisions made (2026-06-14). Ready for a Phase-0 plan.

## Decisions (2026-06-14)

- **Target: public multi-tenant connector with per-user OAuth identity.** All
  four phases below are in scope; the end-state is one public endpoint serving
  many users, each acting as their own workbench identity.
- **`tti-workbench-server` may change.** This unlocks the cleanest identity story
  — **the workbench fronts OAuth / issues per-user tokens** (crux option 2 below),
  so the connector *delegates* auth instead of custody-ing user API keys. This is
  therefore a **two-repo effort** (`TTIO-MCP-Server` + `tti-workbench-server`).
- Custody-based fallback (crux option 1) is kept only as a contingency if the
  workbench OAuth work slips; single service account (option 3) is **out**
  (no per-user identity).
- Still open: hosting target + operational ownership (see end).

## Goal

Today `ttio-mcp` is a **stdio** server: every user installs it locally and their
MCP client spawns it as a subprocess. "One-click connector" means a user adds
`ttio-mcp` to Claude (or another MCP host) by **URL**, authenticates in the
browser, and starts using it — no local install, no Python, no `claude mcp add`.

Two distinct end-states are often conflated; pick deliberately:

- **Custom connector (by URL).** Claude (Pro/Team/Enterprise) lets a user paste a
  remote MCP server URL and connect. This is the realistic, self-serve target —
  we control it entirely.
- **Curated directory listing** (like "Open Targets", "PubMed"). That is an
  Anthropic-curated partner directory; you do **not** self-add. It requires a
  remote connector that already works as a custom connector **plus** a
  partnership/submission process with Anthropic. Treat it as a follow-on to the
  custom connector, not a build task.

This scope targets the **custom-connector-by-URL** end-state.

## The gap (current → required)

| Dimension | Today (v0.9.0) | Required for a remote connector |
|---|---|---|
| Transport | stdio only (`mcp.server.stdio`) | **Streamable HTTP** (the `mcp` SDK's `streamable-http`), TLS, public URL |
| Tenancy | **single** process-global session (`CONN = ConnectionManager()`, one `self._client`) | **multi-tenant**: one workbench client *per connected user/session* |
| Auth | `ttio_login` (user/pass/TOTP) tool, or `TTIO_WB_TOKEN` env | **OAuth 2.1** authorization (the MCP remote-auth spec): protected-resource metadata, dynamic client registration, browser consent — or a token-based custom-connector auth |
| Identity | the operator's single account | each Claude user maps to **their own** workbench identity |
| Hosting | none (local subprocess) | a deployed, monitored HTTPS service |
| State/secrets | one token in memory | per-session tokens, isolation, lifecycle, revocation |

The `mcp` SDK in use (1.27.2) already supports `streamable-http` and ships an
auth framework, so the transport and OAuth scaffolding are available — the work
is in **tenancy** and **identity mapping**, not protocol plumbing.

## The crux: identity mapping

This is the hard design decision, not the transport. When a Claude user connects,
the connector must obtain a **workbench identity** for them. The workbench
authenticates with **username + password + TOTP** or an **API key**
(`ttiowbk_…`) — it is not itself an OAuth provider. Options:

1. **Connector-issued OAuth, user supplies a workbench API key once.** The OAuth
   consent screen (our auth server) collects/stores the user's workbench API key,
   bound to their connector identity. Tools then act as that workbench user.
   *Pro:* real per-user identity; *Con:* we store user API keys (custody, rotation,
   revocation); we must run an OAuth authorization server.
2. **Workbench becomes/【fronts】an OAuth provider.** Add an OAuth front to
   `tti-workbench-server` (or an IdP in front of it) so the connector delegates
   auth entirely. *Pro:* cleanest identity story, no key custody in the connector;
   *Con:* significant `tti-workbench-server` work — out of this repo's scope.
3. **Single service account (no per-user identity).** The connector authenticates
   to the workbench as **one** service account; every Claude user shares it.
   *Pro:* far simpler — no OAuth-to-workbench mapping; *Con:* no per-user
   permissions/audit; only acceptable for a single-team/trusted deployment.

**Chosen (2026-06-14): option 2** — the workbench fronts OAuth / issues per-user
tokens, so the connector delegates auth and never custodies user keys. Enabled by
the decision that `tti-workbench-server` may change. Option 1 is the contingency
if that workbench work slips; option 3 is out (no per-user identity).

This makes Phase 2 a **cross-repo** effort: `tti-workbench-server` gains an OAuth
authorization surface (or an IdP in front of it); `ttio-mcp` becomes an OAuth
**resource server** that validates those tokens and maps each to a per-session
workbench client. The OAuth + workbench-IdP design is itself a sub-project worth
its own brainstorm before a plan.

## Architecture changes (in this repo)

Regardless of identity choice, the server must change shape:

1. **Per-session connection registry.** Replace the global `CONN` singleton with
   a registry keyed by MCP session id (and/or authenticated principal), each
   holding its own `WorkbenchClient`. Every tool currently calls
   `CONN.require_client()`; route that through the request's session instead.
   This touches all seven tool modules (each `register(app, CONN, CONFIG)`).
2. **HTTP transport entrypoint.** Add a `streamable-http` run path alongside
   stdio (keep stdio for local use). Concurrency: the workbench client + tools
   are currently written for one event loop / one session; audit for shared
   mutable state and blocking calls under concurrent sessions.
3. **Auth layer.** Wire the `mcp` SDK auth provider: protected-resource metadata,
   token verification, and the chosen identity mapping (see crux). Tokens/keys
   per session, with expiry + revocation, never logged.
4. **Session lifecycle.** Idle eviction, max sessions, clean teardown of
   `WorkbenchClient`s (the SDK already cautioned about non-daemon WS threads —
   see the prior leak fix; multi-session makes leak-hygiene mandatory).
5. **Ops surface.** Health/readiness endpoints, structured logging without
   secrets, rate limiting, and configuration for the public URL/TLS.

## Hosting & operations (outside the package)

- A deployment target (container on a cloud VM / managed runtime) with TLS and a
  stable public hostname.
- Network egress to the `tti-workbench-server` (the connector is a client of it).
- Secrets management for any stored workbench credentials (option 1) or the
  service-account key (option 3).
- Monitoring, log retention, and an abuse/rate-limit story (a public endpoint is
  internet-reachable).

## Decision forks — RESOLVED

- Deployment/tenancy: **multi-tenant hosted** (public endpoint, per-user identity).
- Identity mapping: **option 2** (workbench fronts OAuth) — see crux.
- Auth mechanism: **full OAuth 2.1** (MCP remote-auth spec).

See "Decisions (2026-06-14)" above. The only remaining open input is hosting +
operational ownership.

## Phased recommendation

A staged path that delivers value early and defers the heaviest work:

- **Phase 0 — Remote transport (single-tenant).** Add `streamable-http`, keep the
  existing single-session model and a service-account/operator login, deploy one
  instance behind TLS, and add it to Claude as a custom connector by URL. Proves
  the remote path end-to-end with the least new surface. *(Identity option 3.)*
- **Phase 1 — Per-session tenancy.** Replace the global `CONN` with a per-session
  registry; concurrency-audit the tools; lifecycle + leak hygiene. No auth change
  yet (still service-account or a shared token), but now safe for concurrent
  users.
- **Phase 2 — OAuth + per-user identity.** Stand up the connector's OAuth
  authorization (option 1: user supplies a workbench API key at consent),
  per-user workbench clients, key custody + revocation. This is the real
  multi-tenant connector.
- **Phase 3 — Hardening & (optional) directory submission.** Rate limiting,
  observability, security review; then pursue Anthropic directory listing if
  desired.

**Recommendation:** start with **Phase 0** to validate remote + custom-connector
mechanics cheaply, *then* decide whether the audience justifies Phases 2–3.
For an internal/single-team need, Phase 0–1 may be the whole project.

## Effort & risk (rough)

- Phase 0: small — transport + deploy; mostly config + one entrypoint.
- Phase 1: medium — touches all tool modules + concurrency audit; real test work.
- Phase 2: large — OAuth authorization server, credential custody, security
  review; partly depends on decisions in `tti-workbench-server`.
- Phase 3: medium/ongoing + an external (Anthropic) dependency for the directory.

**Top risks:** (a) credential custody if we store user workbench keys; (b)
concurrency bugs from the single-session→multi-session change; (c) the directory
listing is gated by an external party and should not be on the critical path.

## Open questions for the user

1. ~~Audience~~ — **resolved: public multi-tenant.**
2. ~~Identity~~ — **resolved: per-user OAuth, workbench fronts OAuth (option 2).**
3. ~~Changing `tti-workbench-server`~~ — **resolved: yes, on the table.**
4. **Hosting + ownership (still open):** where does the public endpoint run, and
   who operates it (deploy target, TLS/hostname, secrets, monitoring, on-call)?

## Recommended next step

Two pieces of design precede coding:

- **Brainstorm the cross-repo OAuth identity design** (workbench-as-OAuth-provider
  vs. an IdP in front; token format; how an OAuth principal maps to a workbench
  account; revocation) — this is the riskiest unknown and spans both repos.
- **Write a Phase-0 plan** (remote `streamable-http` transport + deploy one
  instance, still single-session) to prove the remote path while the OAuth design
  settles.

Phase 0 can proceed in parallel with the OAuth design since it does not depend on
the identity decision.
