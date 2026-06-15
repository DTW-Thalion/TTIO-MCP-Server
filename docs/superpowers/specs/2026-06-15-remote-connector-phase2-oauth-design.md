# Remote Connector — Phase 2 (Per-user OAuth identity) Design

**Status:** approved design (2026-06-15). Architecture spec for the cross-repo
Phase 2 work; implementation decomposes into three sub-projects, each with its
own plan. Builds on Phase 0 (remote streamable-HTTP transport) and Phase 1
(per-session tenancy), both merged. See `docs/remote-connector-scope.md`.

## Goal

Give each Claude connector user their **own workbench identity** via OAuth, so a
single public `ttio-mcp` instance is a true multi-tenant connector. The
workbench (`tti-workbench-server`) remains the **identity/credential authority**;
Keycloak provides the OAuth protocol; `ttio-mcp` is an OAuth resource server that
brokers to the workbench as the authenticated user.

## Decisions (2026-06-15)

1. **Authorization server: Keycloak in front** (not a hand-rolled ObjC AS, not a
   hosted SaaS IdP). Keycloak owns OAuth 2.1 + Dynamic Client Registration (DCR)
   + PKCE.
2. **Workbench is the identity authority via federation** — a custom Keycloak
   authenticator verifies credentials against the workbench's existing
   `/v1/auth/login` (password + TOTP). Users keep their TTI-O credentials; no
   separate user store.
3. **Token handoff: RFC 8693 token exchange** — `ttio-mcp` exchanges the user's
   access token for a workbench-audience token at Keycloak, then presents that
   to the workbench. Chosen for clean audience separation and OAuth-native
   lifecycle over embedding a workbench token in a claim.

## Components

- **Keycloak** (realm `ttio`) — OAuth 2.1 AS: `/authorize`, `/token`, DCR,
  `.well-known` metadata, PKCE; a **custom federation authenticator** (Java SPI)
  delegating login to the workbench; **token-exchange** permission scoped to the
  `ttio-mcp` client.
- **`ttio-mcp`** (Python/FastMCP) — OAuth **resource server**: protected-resource
  metadata, access-token validation, token exchange, and the Phase-1 per-session
  registry keyed by MCP session.
- **`tti-workbench-server`** (ObjC) — two roles: the existing `/v1/auth/login`
  the Keycloak authenticator calls, and a **new JWT bearer auth path** that
  validates workbench-audience Keycloak tokens.
- **Claude** — the MCP client that drives the OAuth flow (DCR + auth-code +
  PKCE).

## End-to-end flow

1. User adds the connector URL (`https://<host>/mcp`) in Claude. `ttio-mcp`
   replies `401` with `WWW-Authenticate` referencing
   `/.well-known/oauth-protected-resource`, which names Keycloak as the AS.
2. Claude reads Keycloak's AS metadata, performs **DCR**, then the **auth-code +
   PKCE** flow, redirecting the user to Keycloak `/authorize`.
3. Keycloak's **custom authenticator** prompts for TTI-O username/password/TOTP
   and verifies them via the workbench `/v1/auth/login`. On success Keycloak sets
   the identity's `sub` to the workbench account and issues an authorization
   code → Claude exchanges it for an **access token (`aud=ttio-mcp`)**.
4. Claude calls `ttio-mcp` tools with that token. `ttio-mcp` validates it (JWKS,
   `iss`=Keycloak, `aud`=`ttio-mcp`, `exp`/sig).
5. `ttio-mcp` performs **RFC 8693 token exchange** at Keycloak (subject = user's
   token, audience = `tti-workbench`) → a **workbench-audience token**.
6. `ttio-mcp` builds/uses a **per-session** workbench client (Phase-1 registry,
   keyed by session) with the exchanged token as bearer.
7. The workbench **validates the workbench-audience JWT** (JWKS, `iss`=Keycloak,
   `aud`=`tti-workbench`), maps `sub`→TTI-O account, and authorizes the call.
8. On expiry, Claude refreshes via Keycloak; `ttio-mcp` re-exchanges as needed.

Note the benign two-way relationship: Keycloak delegates *login* to the
workbench (step 3) while the workbench trusts Keycloak *tokens* (step 7) — two
distinct flows that compose cleanly.

## Token contract (shared interface — define once, build to it)

| Field | Value |
|---|---|
| Issuer (`iss`) | `https://<keycloak>/realms/ttio` |
| User access-token audience | `ttio-mcp` |
| Token-exchange result audience | `tti-workbench` |
| Subject (`sub`) | the TTI-O workbench account id; `preferred_username` carries the username |
| Validation inputs | Keycloak JWKS URL, `iss`, expected `aud`, `exp`/`nbf`, signature |
| Allowed signing algs | RS256 / ES256 (reject `alg=none`) |
| Scope | `ttio.connector` (gates connector access) |

Fixing this contract is what lets the three sub-projects proceed independently.

## Decomposition (three sub-projects)

### SP1 — Keycloak realm + federation authenticator + token-exchange *(foundation; first)*
- Stand up Keycloak (realm `ttio`); enable the token-exchange feature.
- **Custom authenticator SPI (Java):** at `/authorize`, prompt for TTI-O
  username/password/TOTP and verify via the workbench `/v1/auth/login`; set the
  Keycloak identity's `sub` to the workbench account.
- Clients: a confidential `ttio-mcp` client permitted to token-exchange to the
  `tti-workbench` audience; DCR enabled (with a registration policy) for public
  clients (Claude).
- Lives in `tti-workbench-server/keycloak/` (config-as-code + the Java SPI), as
  it is tightly coupled to the workbench login API.

### SP2 — Workbench JWT bearer auth path (ObjC) *(parallel after SP1)*
- New mode in `TTIOWBAuthMiddleware`: accept `Authorization: Bearer <jwt>`,
  validate via Keycloak JWKS (`iss`, `aud=tti-workbench`, `exp`, sig, pinned
  algs), map `sub`→account, populate the existing `TTIOWBAuthContext` — alongside
  the current `ttiowbs_`/`ttiowbk_` paths. JWKS fetch + cache + rotation.

### SP3 — `ttio-mcp` OAuth resource server (Python) *(parallel after SP1)*
- Serve `/.well-known/oauth-protected-resource` → Keycloak; `401 +
  WWW-Authenticate` when unauthenticated.
- Validate the user's access token via the mcp SDK `TokenVerifier` (JWKS,
  `aud=ttio-mcp`).
- **Token-exchange client:** swap the validated token for a `tti-workbench`
  -audience token at Keycloak `/token`.
- Build the per-session workbench client (Phase-1 registry) from the exchanged
  token; cache it per session with refresh.

### Ordering & relationships
- **SP1 first** — it issues the tokens the other two consume and provides the
  token-exchange config SP3 needs.
- **SP2 and SP3 in parallel** afterward — each only needs to trust Keycloak (the
  contract + JWKS); they are independent of each other.
- **Integration milestone** after all three: full Claude → Keycloak → `ttio-mcp`
  → workbench round-trip.
- Each sub-project gets its own spec/plan → implementation cycle; this document
  is the overarching architecture spec + contract.

## Security

- **PKCE mandatory**; **DCR with a registration policy** (constrained redirect
  URIs / client metadata) so the public registration endpoint cannot be abused.
- **Strict audience separation** — `ttio-mcp` rejects non-`ttio-mcp` audiences;
  the workbench rejects non-`tti-workbench` audiences. The confused-deputy guard
  and the reason token-exchange is used.
- **Token-exchange locked down** — only the confidential `ttio-mcp` client may
  exchange to the `tti-workbench` audience.
- **JWT hardening** (both resource servers): JWKS signature check, `iss` pinning,
  `exp`/`nbf` with small clock skew, pinned algs (RS256/ES256), reject
  `alg=none`; JWKS cached with rotation.
- **Lifecycle:** short access-token TTL + rotating refresh tokens. Revoking the
  Keycloak session or disabling the workbench account stops new tokens; short
  TTLs bound the blast radius.
- **Credential handling:** the authenticator forwards password/TOTP to
  `/v1/auth/login` over TLS only and stores no passwords; Keycloak brute-force
  detection + the workbench's existing rate-limit middleware both apply. **No
  tokens are ever logged.**
- **Posture preserved:** the `ttio.connector` scope gates access; the connector
  still exposes only the **28 non-admin tools** (no admin surface).
- TLS everywhere; the connector URL must be HTTPS.

## Testing

- **SP1 (Keycloak/Java):** unit-test the authenticator SPI with a mocked
  workbench-login client (success / bad password / bad TOTP / workbench-down);
  validate the realm via config import; a `testcontainers` Keycloak smoke running
  `/authorize`→`/token`.
- **SP2 (workbench/ObjC):** unit tests over locally-signed JWTs with a fixture
  key — accept valid; reject expired / wrong-`aud` / wrong-`iss` / bad-sig /
  `alg=none`; verify `sub`→account mapping; folded into the existing ObjC suite.
- **SP3 (`ttio-mcp`/Python):** unit tests for protected-resource metadata, token
  validation (mocked JWKS), token-exchange (mocked Keycloak `/token`), and the
  per-session client built from the exchanged token; an opt-in **authenticated
  HTTP smoke** extending the existing `tests/integration/test_http_smoke.py`
  pattern against a real (docker) Keycloak.
- **Integration:** a `docker-compose` harness (Keycloak + workbench + `ttio-mcp`)
  driving the full flow — assert a tool call succeeds end-to-end as a specific
  user, and that audience/issuer mismatches are rejected.

## Out of scope (later)

- **Hosting / operational ownership** of the public endpoint + Keycloak (deploy
  target, TLS/hostname, secrets, monitoring, on-call) — still an open decision,
  tracked in the scope doc; needed before a production launch but not for the
  build.
- **Anthropic curated-directory** submission (Phase 3) — a partnership process,
  external to this build.
- Rate limiting / observability / security review hardening beyond the basics
  above — Phase 3.
- SSO / social login via Keycloak — possible later given the IdP, not in scope
  now.

## Open items

- **Hosting & ownership** (above) — decide before production.
- Keycloak deployment shape (container alongside the workbench vs standalone) —
  settle in SP1's plan.
- Whether `sub` should be the workbench account id or username — settle in SP1
  when the authenticator is built (the contract above assumes id, username in
  `preferred_username`).
