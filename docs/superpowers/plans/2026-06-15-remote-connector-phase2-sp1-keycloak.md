# Phase 2 · SP1 — Keycloak realm + federation authenticator + token-exchange

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up Keycloak as the OAuth 2.1 authorization server for the remote connector, with a custom authenticator that federates login to the workbench's `/v1/auth/login` (password + TOTP) and a confidential `ttio-mcp` client permitted to RFC 8693 token-exchange to a `tti-workbench` audience — the foundation SP2 and SP3 build against.

**Architecture:** A small Maven project (`tti-workbench-server/keycloak/authenticator/`) builds a Keycloak provider JAR containing a custom `Authenticator` (+ `AuthenticatorFactory`) that renders a username/password/TOTP form and validates it by calling the workbench login API over HTTP; on success it sets a transient Keycloak user whose `sub` is the workbench `user_id`. A realm export (`keycloak/realm/ttio-realm.json`) wires the authenticator into a browser flow and defines the `ttio-mcp` (confidential, token-exchange) and DCR client policy, with audience protocol-mappers for `ttio-mcp` and `tti-workbench`. A `docker-compose` brings up Keycloak with the JAR mounted and the realm imported.

**Tech Stack:** Keycloak 26.x, Java 17, Maven, Keycloak Authenticator SPI, RFC 8693 token exchange (Keycloak `token-exchange` + `admin-fine-grained-authz` preview features), JUnit 5 + a stub HTTP server for tests, Docker.

**Spec:** `docs/superpowers/specs/2026-06-15-remote-connector-phase2-oauth-design.md`. **Workbench login contract:** `POST /v1/auth/login` with JSON `{username, password, totp}` → `200 {token, user_id, username, expires_at}` or `401` (invalid credentials / invalid TOTP).

---

## Context & key facts

- Lives in the **`tti-workbench-server`** repo under `keycloak/` (tightly coupled to the workbench login API). This plan doc lives with the connector planning trail in `TTIO-MCP-Server/docs/superpowers/plans/`.
- Keycloak loads provider JARs from `/opt/keycloak/providers/`; an `AuthenticatorFactory` is discovered via `META-INF/services/org.keycloak.authentication.AuthenticatorFactory`.
- Token exchange in KC 26 is gated behind the `token-exchange` (and `admin-fine-grained-authz`) preview features (`KC_FEATURES=token-exchange,admin-fine-grained-authz` / `--features=...`).
- Transient (non-stored) users: KC 26 exposes `LightweightUserAdapter`; the authenticator sets the user on the `AuthenticationFlowContext` so no local user store is needed.
- The token contract (audiences `ttio-mcp` / `tti-workbench`, `sub`=workbench `user_id`, `preferred_username`=username, scope `ttio.connector`, RS256) is fixed by the spec.

## File Structure (all under `tti-workbench-server/`)

- `keycloak/authenticator/pom.xml` — Maven build for the provider JAR (provided-scope Keycloak SPI deps + JUnit).
- `keycloak/authenticator/src/main/java/global/thalion/ttio/keycloak/WorkbenchLoginClient.java` — HTTP client for `/v1/auth/login`; returns a result object or signals invalid creds.
- `keycloak/authenticator/src/main/java/global/thalion/ttio/keycloak/WorkbenchAuthenticator.java` — the `Authenticator`: render form → collect creds → call client → set transient user / fail.
- `keycloak/authenticator/src/main/java/global/thalion/ttio/keycloak/WorkbenchAuthenticatorFactory.java` — the `AuthenticatorFactory` (id `ttio-workbench-login`).
- `keycloak/authenticator/src/main/resources/META-INF/services/org.keycloak.authentication.AuthenticatorFactory` — registration.
- `keycloak/authenticator/src/main/resources/theme-resources/templates/workbench-login.ftl` — the login form (username/password/TOTP).
- `keycloak/authenticator/src/test/java/global/thalion/ttio/keycloak/WorkbenchLoginClientTest.java` — unit tests against a stub HTTP server.
- `keycloak/realm/ttio-realm.json` — realm export: clients, flow binding, mappers.
- `keycloak/docker-compose.yml` — Keycloak 26 with the JAR + realm mounted, features enabled.
- `keycloak/README.md` — build/run/verify runbook.

---

## Task 1: Maven scaffold + WorkbenchLoginClient (HTTP) — TDD

**Files:**
- Create: `keycloak/authenticator/pom.xml`
- Create: `keycloak/authenticator/src/main/java/global/thalion/ttio/keycloak/WorkbenchLoginClient.java`
- Test: `keycloak/authenticator/src/test/java/global/thalion/ttio/keycloak/WorkbenchLoginClientTest.java`

- [ ] **Step 1: Write `pom.xml`**

```xml
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>global.thalion.ttio</groupId>
  <artifactId>ttio-keycloak-authenticator</artifactId>
  <version>0.1.0</version>
  <packaging>jar</packaging>
  <properties>
    <maven.compiler.release>17</maven.compiler.release>
    <keycloak.version>26.0.7</keycloak.version>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.keycloak</groupId><artifactId>keycloak-server-spi</artifactId>
      <version>${keycloak.version}</version><scope>provided</scope>
    </dependency>
    <dependency>
      <groupId>org.keycloak</groupId><artifactId>keycloak-server-spi-private</artifactId>
      <version>${keycloak.version}</version><scope>provided</scope>
    </dependency>
    <dependency>
      <groupId>org.keycloak</groupId><artifactId>keycloak-services</artifactId>
      <version>${keycloak.version}</version><scope>provided</scope>
    </dependency>
    <dependency>
      <groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId>
      <version>5.10.2</version><scope>test</scope>
    </dependency>
  </dependencies>
  <build><finalName>ttio-keycloak-authenticator</finalName></build>
</project>
```

- [ ] **Step 2: Write the failing test** (uses the JDK's built-in `HttpServer` as a stub — no extra deps)

```java
// WorkbenchLoginClientTest.java
package global.thalion.ttio.keycloak;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.*;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import static org.junit.jupiter.api.Assertions.*;

class WorkbenchLoginClientTest {
    HttpServer server; String base;

    void start(int status, String body) throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/v1/auth/login", ex -> {
            byte[] b = body.getBytes(StandardCharsets.UTF_8);
            ex.sendResponseHeaders(status, b.length);
            ex.getResponseBody().write(b); ex.close();
        });
        server.start();
        base = "http://127.0.0.1:" + server.getAddress().getPort();
    }

    @AfterEach void stop() { if (server != null) server.stop(0); }

    @Test void valid_credentials_return_identity() throws Exception {
        start(200, "{\"token\":\"ttiowbs_x\",\"user_id\":\"u1\",\"username\":\"alice\",\"expires_at\":99}");
        var r = new WorkbenchLoginClient(base).login("alice", "pw", "123456");
        assertTrue(r.ok());
        assertEquals("u1", r.userId());
        assertEquals("alice", r.username());
    }

    @Test void invalid_credentials_return_not_ok() throws Exception {
        start(401, "{\"error\":\"invalid credentials\"}");
        var r = new WorkbenchLoginClient(base).login("alice", "bad", "000000");
        assertFalse(r.ok());
    }
}
```

- [ ] **Step 3: Run — expect compile/test failure** (`WorkbenchLoginClient` missing)

Run: `cd keycloak/authenticator && mvn -q test`

- [ ] **Step 4: Implement `WorkbenchLoginClient`**

```java
// WorkbenchLoginClient.java
package global.thalion.ttio.keycloak;

import java.net.URI;
import java.net.http.*;
import java.time.Duration;

public class WorkbenchLoginClient {
    public record Result(boolean ok, String userId, String username) {}
    private final String baseUrl;
    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5)).build();

    public WorkbenchLoginClient(String baseUrl) { this.baseUrl = baseUrl; }

    public Result login(String username, String password, String totp) {
        String body = String.format(
            "{\"username\":%s,\"password\":%s,\"totp\":%s}",
            jsonStr(username), jsonStr(password), jsonStr(totp));
        HttpRequest req = HttpRequest.newBuilder(URI.create(baseUrl + "/v1/auth/login"))
            .header("Content-Type", "application/json")
            .timeout(Duration.ofSeconds(10))
            .POST(HttpRequest.BodyPublishers.ofString(body)).build();
        try {
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() != 200) return new Result(false, null, null);
            String b = resp.body();
            return new Result(true, jsonField(b, "user_id"), jsonField(b, "username"));
        } catch (Exception e) {
            return new Result(false, null, null);
        }
    }

    // Minimal extractors — the response is a small flat JSON object.
    private static String jsonStr(String s) {
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }
    private static String jsonField(String json, String key) {
        String needle = "\"" + key + "\"";
        int i = json.indexOf(needle); if (i < 0) return null;
        int c = json.indexOf(':', i + needle.length()); if (c < 0) return null;
        int q1 = json.indexOf('"', c + 1); if (q1 < 0) return null;
        int q2 = json.indexOf('"', q1 + 1); if (q2 < 0) return null;
        return json.substring(q1 + 1, q2);
    }
}
```

Helper accessors used by the test: add `ok()`, `userId()`, `username()` are the record accessors (records generate them automatically — `Result` exposes `ok()/userId()/username()`).

- [ ] **Step 5: Run — expect pass**

Run: `cd keycloak/authenticator && mvn -q test`  → 2 tests pass.

- [ ] **Step 6: Commit** (in the `tti-workbench-server` repo)

```bash
git add keycloak/authenticator/pom.xml keycloak/authenticator/src/main/java/global/thalion/ttio/keycloak/WorkbenchLoginClient.java keycloak/authenticator/src/test/java/global/thalion/ttio/keycloak/WorkbenchLoginClientTest.java
git commit -m "feat(keycloak): WorkbenchLoginClient calling /v1/auth/login"
```

## Task 2: Custom Authenticator + Factory + registration

**Files:**
- Create: `WorkbenchAuthenticator.java`, `WorkbenchAuthenticatorFactory.java`
- Create: `src/main/resources/META-INF/services/org.keycloak.authentication.AuthenticatorFactory`
- Create: `src/main/resources/theme-resources/templates/workbench-login.ftl`
- Test: `WorkbenchAuthenticatorDecisionTest.java`

- [ ] **Step 1: Write a decision-logic test** (the Keycloak `AuthenticationFlowContext` is heavy; extract the credential decision into a pure method and test that)

```java
// WorkbenchAuthenticatorDecisionTest.java
package global.thalion.ttio.keycloak;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class WorkbenchAuthenticatorDecisionTest {
    @Test void blank_fields_are_rejected_without_calling_workbench() {
        assertFalse(WorkbenchAuthenticator.hasAllCredentials("", "pw", "123456"));
        assertFalse(WorkbenchAuthenticator.hasAllCredentials("alice", "", "123456"));
        assertFalse(WorkbenchAuthenticator.hasAllCredentials("alice", "pw", ""));
        assertTrue(WorkbenchAuthenticator.hasAllCredentials("alice", "pw", "123456"));
    }
}
```

- [ ] **Step 2: Run — expect fail** (`WorkbenchAuthenticator` missing)

Run: `cd keycloak/authenticator && mvn -q test`

- [ ] **Step 3: Implement the Authenticator**

```java
// WorkbenchAuthenticator.java
package global.thalion.ttio.keycloak;

import jakarta.ws.rs.core.MultivaluedMap;
import jakarta.ws.rs.core.Response;
import org.keycloak.authentication.AuthenticationFlowContext;
import org.keycloak.authentication.AuthenticationFlowError;
import org.keycloak.authentication.Authenticator;
import org.keycloak.models.*;
import org.keycloak.models.light.LightweightUserAdapter;

public class WorkbenchAuthenticator implements Authenticator {
    static boolean hasAllCredentials(String u, String p, String t) {
        return u != null && !u.isBlank() && p != null && !p.isBlank() && t != null && !t.isBlank();
    }

    private WorkbenchLoginClient client(AuthenticationFlowContext ctx) {
        String base = System.getenv().getOrDefault("TTIO_WB_URL", "http://workbench:8443");
        return new WorkbenchLoginClient(base);
    }

    @Override public void authenticate(AuthenticationFlowContext ctx) {
        Response form = ctx.form().createForm("workbench-login.ftl");
        ctx.challenge(form);
    }

    @Override public void action(AuthenticationFlowContext ctx) {
        MultivaluedMap<String, String> f = ctx.getHttpRequest().getDecodedFormParameters();
        String u = f.getFirst("username"), p = f.getFirst("password"), t = f.getFirst("totp");
        if (!hasAllCredentials(u, p, t)) {
            ctx.failureChallenge(AuthenticationFlowError.INVALID_CREDENTIALS,
                ctx.form().setError("missingCredentials").createForm("workbench-login.ftl"));
            return;
        }
        WorkbenchLoginClient.Result r = client(ctx).login(u, p, t);
        if (!r.ok()) {
            ctx.failureChallenge(AuthenticationFlowError.INVALID_CREDENTIALS,
                ctx.form().setError("invalidWorkbenchCredentials").createForm("workbench-login.ftl"));
            return;
        }
        LightweightUserAdapter user = new LightweightUserAdapter(ctx.getSession(), r.userId());
        user.setEnabled(true);
        user.setUsername(r.username());
        ctx.setUser(user);
        ctx.success();
    }

    @Override public boolean requiresUser() { return false; }
    @Override public boolean configuredFor(KeycloakSession s, RealmModel r, UserModel u) { return true; }
    @Override public void setRequiredActions(KeycloakSession s, RealmModel r, UserModel u) {}
    @Override public void close() {}
}
```

> Verify against KC 26.0.7: the `LightweightUserAdapter(session, id)` constructor
> and `ctx.setUser(...)` transient-user path. If the constructor differs in the
> pinned version, adjust to the version's transient-user API; acceptance is the
> Task 4 smoke issuing a token with `sub == user_id`.

- [ ] **Step 4: Implement the Factory + registration + form template**

`WorkbenchAuthenticatorFactory.java`:

```java
package global.thalion.ttio.keycloak;

import org.keycloak.Config;
import org.keycloak.authentication.*;
import org.keycloak.models.*;
import org.keycloak.provider.ProviderConfigProperty;
import java.util.List;

public class WorkbenchAuthenticatorFactory implements AuthenticatorFactory {
    public static final String ID = "ttio-workbench-login";
    private static final WorkbenchAuthenticator SINGLETON = new WorkbenchAuthenticator();

    @Override public String getId() { return ID; }
    @Override public Authenticator create(KeycloakSession session) { return SINGLETON; }
    @Override public String getDisplayType() { return "TTI-O Workbench Login"; }
    @Override public String getReferenceCategory() { return "ttio-workbench"; }
    @Override public boolean isConfigurable() { return false; }
    @Override public AuthenticationExecutionModel.Requirement[] getRequirementChoices() {
        return new AuthenticationExecutionModel.Requirement[] {
            AuthenticationExecutionModel.Requirement.REQUIRED };
    }
    @Override public boolean isUserSetupAllowed() { return false; }
    @Override public String getHelpText() { return "Authenticates against the TTI-O workbench (password + TOTP)."; }
    @Override public List<ProviderConfigProperty> getConfigProperties() { return List.of(); }
    @Override public void init(Config.Scope c) {}
    @Override public void postInit(KeycloakSessionFactory f) {}
    @Override public void close() {}
}
```

`META-INF/services/org.keycloak.authentication.AuthenticatorFactory` (one line):

```
global.thalion.ttio.keycloak.WorkbenchAuthenticatorFactory
```

`theme-resources/templates/workbench-login.ftl`:

```ftl
<#import "template.ftl" as layout>
<@layout.registrationLayout; section>
  <#if section = "form">
    <form action="${url.loginAction}" method="post">
      <input name="username" placeholder="TTI-O username" autofocus/>
      <input name="password" type="password" placeholder="Password"/>
      <input name="totp" placeholder="TOTP code" autocomplete="one-time-code"/>
      <input type="submit" value="Sign in"/>
    </form>
  </#if>
</@layout.registrationLayout>
```

- [ ] **Step 5: Run tests + build the JAR**

Run: `cd keycloak/authenticator && mvn -q test && mvn -q package`
Expected: tests pass; `target/ttio-keycloak-authenticator.jar` produced.

- [ ] **Step 6: Commit**

```bash
git add keycloak/authenticator/src
git commit -m "feat(keycloak): workbench federation authenticator + factory + form"
```

## Task 3: Realm export (clients, audiences, flow binding)

**Files:**
- Create: `keycloak/realm/ttio-realm.json`

- [ ] **Step 1: Write the realm export**

Author `ttio-realm.json` defining realm `ttio` with:
- A **browser flow** copy whose forms step uses the `ttio-workbench-login`
  authenticator (`authenticatorFlow:false`, `requirement:REQUIRED`) and is set as
  the realm `browserFlow`.
- A confidential client `ttio-mcp` (`serviceAccountsEnabled:true`,
  `standardFlowEnabled:true`, `publicClient:false`) with a **token-exchange
  permission** and an **audience mapper** adding `tti-workbench` to exchanged
  tokens and `ttio-mcp` to its own access tokens; assign the `ttio.connector`
  client scope (default).
- A client-registration policy allowing **DCR** for public clients with a
  redirect-URI allowlist for Claude's callback.
- A realm `ttio.connector` client scope with an audience mapper for `ttio-mcp`.

Validate it parses:
Run: `python3 -c "import json,sys; json.load(open('keycloak/realm/ttio-realm.json')); print('realm json OK')"`

- [ ] **Step 2: Commit**

```bash
git add keycloak/realm/ttio-realm.json
git commit -m "feat(keycloak): ttio realm export (clients, audiences, flow)"
```

> The exact JSON for token-exchange permissions + DCR policy is KC-version-
> specific. Author it against KC 26.0.7, then **prove it by import** in Task 4
> (a malformed export fails the import); do not hand-wave the fields.

## Task 4: docker-compose + end-to-end smoke

**Files:**
- Create: `keycloak/docker-compose.yml`
- Create: `keycloak/smoke.sh`
- Create: `keycloak/README.md`

- [ ] **Step 1: Write `docker-compose.yml`**

```yaml
services:
  keycloak:
    image: quay.io/keycloak/keycloak:26.0.7
    command: ["start-dev", "--import-realm"]
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: admin
      KC_FEATURES: "token-exchange,admin-fine-grained-authz,dynamic-client-registration"
      TTIO_WB_URL: "http://host.docker.internal:8443"
    ports: ["8080:8080"]
    extra_hosts: ["host.docker.internal:host-gateway"]
    volumes:
      - ./authenticator/target/ttio-keycloak-authenticator.jar:/opt/keycloak/providers/ttio-keycloak-authenticator.jar:ro
      - ./realm/ttio-realm.json:/opt/keycloak/data/import/ttio-realm.json:ro
```

- [ ] **Step 2: Write `smoke.sh`** (brings KC up, asserts metadata + a token-exchange round-trip against a **stub** workbench login)

The script: starts a tiny stub that answers `POST /v1/auth/login` with `200 {user_id,username}` on :8443; `docker compose up -d`; waits for `http://localhost:8080/realms/ttio/.well-known/openid-configuration` to return 200; obtains a user token via the direct-grant/`ttio-workbench-login` path; performs an RFC 8693 exchange (`grant_type=urn:ietf:params:oauth:grant-type:token-exchange`, `audience=tti-workbench`); asserts the exchanged JWT decodes with `aud` containing `tti-workbench` and `sub` == the stub `user_id`. Tear down at the end.

- [ ] **Step 3: Run the smoke**

Run: `cd keycloak && bash smoke.sh`
Expected: prints `SMOKE OK` — well-known reachable, exchanged token has `aud=tti-workbench` and `sub=<stub user_id>`. This is the acceptance gate for Tasks 2–3 (proves the authenticator sets `sub`, the realm imports, and token-exchange is permitted).

- [ ] **Step 4: Write `README.md`**

Document: `mvn -q package` to build the JAR; `docker compose up`; the realm/clients; how `ttio-mcp` (SP3) and the workbench (SP2) consume the contract (issuer/JWKS/audiences); the preview features required; and pointers to the spec.

- [ ] **Step 5: Commit**

```bash
git add keycloak/docker-compose.yml keycloak/smoke.sh keycloak/README.md
git commit -m "feat(keycloak): docker-compose + token-exchange smoke + runbook"
```

---

## Self-Review

- **Spec coverage:** Keycloak AS + DCR → Task 3 (client-registration policy) + Task 4 (`.well-known`); custom federation authenticator (password+TOTP → `/v1/auth/login`, `sub`=`user_id`) → Tasks 1–2; token-exchange permission scoped to `ttio-mcp` → Task 3 + proven in Task 4; audiences `ttio-mcp`/`tti-workbench` → Task 3 mappers + Task 4 assertion; token contract (`sub`, `preferred_username`, scope) → Tasks 2–3.
- **Placeholder scan:** the two KC-version-specific spots (transient-user API in Task 2; token-exchange/DCR realm JSON in Task 3) are explicit *verify-by-smoke* steps with a concrete acceptance gate (Task 4 `smoke.sh`), not vague TODOs; all Java code is complete.
- **Type consistency:** `WorkbenchLoginClient` / `Result.ok()/userId()/username()`, `WorkbenchAuthenticator.hasAllCredentials(...)`, factory id `ttio-workbench-login`, and audiences `ttio-mcp`/`tti-workbench` are used identically across tasks and match the spec's token contract.

## Out of scope (other sub-projects / phases)

- **SP2** — workbench JWT bearer auth path (ObjC). **SP3** — `ttio-mcp` OAuth resource server (Python). Both consume this realm's tokens.
- Production Keycloak hardening (HA, external DB, real TLS certs, secrets management) and hosting — Phase 3 / the open hosting decision.
