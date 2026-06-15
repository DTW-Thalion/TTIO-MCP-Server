# Phase 2 · SP2 — Workbench JWT bearer auth path (ObjC) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `tti-workbench-server` accept `Authorization: Bearer <jwt>` where the JWT is a Keycloak-issued `tti-workbench`-audience token, validate it (JWKS signature, `iss`, `aud`, `exp`/`nbf`, pinned algs RS256/ES256, reject `alg=none`), map `sub`→workbench account, and populate the existing `TTIOWBAuthContext` — alongside the current `ttiowbs_`/`ttiowbk_` paths.

**Architecture:** A new JWT branch in `TTIOWBAuthService -contextForRawToken:` (the existing prefix-dispatch site) detects a JWT (three dot-separated base64url segments) and delegates to a new `TTIOWBJwtVerifier`. The verifier parses the JWT, looks up the signing key by `kid` from a `id<TTIOWBJwksSource>`, verifies the signature with OpenSSL `EVP_DigestVerify*`, and validates claims. The JWKS source is a cache (`TTIOWBJwksCache`) over an injectable `id<TTIOWBHttpGetter>` (libcurl in production, a fake in tests). On success the verifier returns claims; the service resolves `sub`→`TTIOWBUserRecord` via `-userForId:` and builds the context with `provider:@"jwt"`. A new optional `keycloak` config block enables/configures the path; absent ⇒ JWT path disabled (tokens fall through to the existing `nil`→401).

**Tech Stack:** Objective-C (GNUstep, ARC for library / `-fno-objc-arc` for tests), OpenSSL 3 (`EVP_PKEY_fromdata`, `EVP_DigestVerify*`, `ECDSA_SIG`), libcurl (synchronous `curl_easy_perform` for the JWKS GET), `NSJSONSerialization`.

**Token contract (from the Phase 2 design spec):** `iss = https://<keycloak>/realms/ttio`; `aud` must contain `tti-workbench`; `sub` = the workbench account ULID (`preferred_username` carries the username); algs RS256/ES256; reject `alg=none`; small clock skew on `exp`/`nbf`; scope `ttio.connector`.

---

## Context & key facts (verified by code exploration, 2026-06-15)

All paths under `\\wsl.localhost\Ubuntu\home\toddw\tti-workbench-server` (build in WSL: `bash scripts/build.sh check`; edit via UNC; push via Windows git per the project notes).

- **Dispatch site:** `Source/Auth/TTIOWBAuthService.m:190-220` — `-contextForRawToken:error:`. Prefix constants `ttiowbs`/`ttiowbk` at `:17-18`. A JWT matches neither `hasPrefix:` and falls through to `return nil;` at `:219`. **Add the JWT branch here.**
- **Context builder:** `-_contextForUser:provider:sessionId:keyId:` at `TTIOWBAuthService.m:41-67`. Reuse it: `provider:@"jwt"`, `sessionId:nil`, `keyId:nil`.
- **Account lookup:** `-[TTIOWBSessionStore userForId:error:]` at `Source/Auth/TTIOWBSessionStore.m:185-196`; `TTIOWBUserRecord` (`TTIOWBSessionStore.h:38-49`) has `userId` (26-char ULID string), `username`, `capabilities`, `projects`, `disabledAt` (`0` ⇒ active). The login `user_id` is exactly this ULID, which is what Keycloak puts in `sub` — so `sub`→`-userForId:` is a direct lookup.
- **Middleware:** `Source/Auth/TTIOWBAuthMiddleware.m:27-83` is prefix-agnostic; it extracts the bearer value and calls `service contextForRawToken:`. **No change needed.**
- **OpenSSL:** linked everywhere (`Source/GNUmakefile:224` `-lssl -lcrypto`). Version **3.x** (`libcrypto.so.3`); use `EVP_PKEY_fromdata` (not deprecated `RSA_set0_key`). SHA-256 EVP idiom to mirror: `TTIOWBSessionStore.m:52`.
- **base64url:** encode-only helper exists (`TTIOWBSessionStore.m:36`, private). **Decode is net-new.**
- **libcurl:** present (`/usr/lib/x86_64-linux-gnu/libcurl.so` + `.so.4`). Add `-lcurl` to `Source/GNUmakefile`, `Tools/GNUmakefile`, `Tests/GNUmakefile`. CI must install `libcurl4-openssl-dev`.
- **JSON:** `NSJSONSerialization` with `isKindOfClass:` guards (`Source/Core/TTIOWBConfig.m:52`).
- **Config:** `+[TTIOWBConfig configFromDictionary:error:]` (`TTIOWBConfig.m:67`), optional nested block pattern modeled on `key_custody` (`TTIOWBConfig.m:314-430`); error enum `TTIOWBConfigError` (`TTIOWBConfig.h:22-28`). Properties declared in `TTIOWBConfig.h`.
- **Build:** Source files listed in `Source/GNUmakefile` (`libTtioWB_HEADER_FILES` ~20-117, `libTtioWB_OBJC_FILES` ~119-213). Library is ARC.
- **Tests:** single runner `Tests/TtioWBTests.m`, compiled **`-fno-objc-arc`** (`Tests/GNUmakefile:44-45`). Forward-declare `static void test_xxx(void);`, define it, then call inside `START_SET("…")/END_SET("…")` in `main()` (`:17534`). Auth fixture `makeAuthFixture()` (`:12034`), `_seedLoginUser()` (`:12050`), request builder `_makeGetRequest(path, authHeader)` (`:12205`), direct service call `[f.svc contextForRawToken:… error:&e]`. Run: `bash scripts/build.sh check`.
- **Daemon bootstrap:** `Tools/TtioWBServer.m` builds the config + services at startup; wire the verifier there.

## File Structure

New files (all under `Source/Auth/`, added to `Source/GNUmakefile`):
- `TTIOWBBase64Url.{h,m}` — `TTIOWBBase64UrlDecode(NSString*)`→`NSData*` and `…Encode(NSData*)`→`NSString*`. One responsibility: base64url ⇄ data.
- `TTIOWBJwks.{h,m}` — JWK/JWKS model + `EVP_PKEY` construction from RSA(`n`,`e`)/EC(`crv`,`x`,`y`); defines `id<TTIOWBJwksSource>` (`-(void*)evpKeyForKid:(NSString*)kid error:`).
- `TTIOWBJwtClaims.{h,m}` — value object for validated claims (`sub`, `preferredUsername`, `audiences`, `scope`, `expiresAt`, `notBefore`, `issuer`) + the pure claim-validation function.
- `TTIOWBJwtVerifier.{h,m}` — parse JWT, pick key by `kid`, verify RS256/ES256 signature, run claim validation; returns `TTIOWBJwtClaims*` or `nil`+`NSError`.
- `TTIOWBHttpGetter.h` — `@protocol TTIOWBHttpGetter` (`-(NSData*)getURL:(NSString*)url error:`).
- `TTIOWBCurlHttpGetter.{h,m}` — libcurl implementation of `TTIOWBHttpGetter`.
- `TTIOWBJwksCache.{h,m}` — implements `TTIOWBJwksSource`; holds a parsed JWKS, refreshes via `id<TTIOWBHttpGetter>` on unknown-`kid` or TTL expiry.

Modified:
- `Source/Core/TTIOWBConfig.{h,m}` — `keycloak` config block.
- `Source/Auth/TTIOWBAuthService.{h,m}` — optional `jwtVerifier`; JWT branch in `-contextForRawToken:`.
- `Tools/TtioWBServer.m` — construct the verifier+cache+curl getter from config and inject into the auth service.
- `Source/GNUmakefile`, `Tools/GNUmakefile`, `Tests/GNUmakefile` — new files + `-lcurl`.
- `Tests/TtioWBTests.m` — new test sets.
- CI workflow under `.github/workflows/` — install `libcurl4-openssl-dev`.

---

## Task 1: `keycloak` config block

**Files:**
- Modify: `Source/Core/TTIOWBConfig.h` (add properties next to the `key_custody` group, after ~line 144)
- Modify: `Source/Core/TTIOWBConfig.m` (parse block after the `key_custody` block, before `return cfg;` ~line 432)
- Test: `Tests/TtioWBTests.m`

- [ ] **Step 1: Write the failing tests** (add forward decls + `START_SET` in `main`)

```objc
// keycloak config block
static void test_config_keycloak_absent_is_nil(void) {
    NSDictionary *d = @{ @"db": @{@"driver": @"sqlite", @"path": @":memory:"} };
    NSError *e = nil;
    TTIOWBConfig *c = [TTIOWBConfig configFromDictionary:d error:&e];
    PASS(c != nil, "config without keycloak parses");
    PASS(c.keycloakEnabled == NO, "keycloak disabled when block absent");
    PASS(c.keycloakIssuer == nil, "issuer nil when absent");
}

static void test_config_keycloak_parsed(void) {
    NSDictionary *d = @{
        @"db": @{@"driver": @"sqlite", @"path": @":memory:"},
        @"keycloak": @{
            @"issuer": @"https://kc.example/realms/ttio",
            @"jwks_url": @"https://kc.example/realms/ttio/protocol/openid-connect/certs",
            @"audience": @"tti-workbench",
            @"allowed_algs": @[@"RS256", @"ES256"]
        }
    };
    NSError *e = nil;
    TTIOWBConfig *c = [TTIOWBConfig configFromDictionary:d error:&e];
    PASS(c != nil, "config with keycloak parses (err=%@)", e);
    PASS(c.keycloakEnabled == YES, "keycloak enabled");
    PASS([c.keycloakIssuer isEqualToString:@"https://kc.example/realms/ttio"], "issuer");
    PASS([c.keycloakAudience isEqualToString:@"tti-workbench"], "audience");
    PASS(c.keycloakAllowedAlgs.count == 2, "two algs");
}

static void test_config_keycloak_missing_issuer_errors(void) {
    NSDictionary *d = @{
        @"db": @{@"driver": @"sqlite", @"path": @":memory:"},
        @"keycloak": @{ @"audience": @"tti-workbench" }
    };
    NSError *e = nil;
    TTIOWBConfig *c = [TTIOWBConfig configFromDictionary:d error:&e];
    PASS(c == nil, "missing issuer rejected");
    PASS(e != nil, "error returned");
}
```

Register in `main`:
```objc
START_SET("SP2 keycloak config")
    test_config_keycloak_absent_is_nil();
    test_config_keycloak_parsed();
    test_config_keycloak_missing_issuer_errors();
END_SET("SP2 keycloak config")
```

- [ ] **Step 2: Run — expect FAIL** (`keycloakEnabled` etc. unknown)

Run: `bash scripts/build.sh check 2>&1 | grep -E "keycloak|error:"`
Expected: compile error — properties not declared.

- [ ] **Step 3: Add properties to `TTIOWBConfig.h`** (after the `key_custody` group)

```objc
// keycloak.* -- SP2 JWT bearer auth (optional; when present, enables the JWT path)
@property (nonatomic, readonly) BOOL keycloakEnabled;
@property (nonatomic, copy, readonly, nullable) NSString *keycloakIssuer;
@property (nonatomic, copy, readonly, nullable) NSString *keycloakJwksUrl;
@property (nonatomic, copy, readonly, nullable) NSString *keycloakAudience;
@property (nonatomic, copy, readonly, nullable) NSArray<NSString *> *keycloakAllowedAlgs;
```

- [ ] **Step 4: Add the parse block to `TTIOWBConfig.m`** (after the `key_custody` block, before `return cfg;`)

```objc
// keycloak.* -- SP2
NSDictionary *keycloak = dict[@"keycloak"];
if (keycloak) {
    if (![keycloak isKindOfClass:[NSDictionary class]]) {
        if (error) *error = [self errorWithCode:TTIOWBConfigErrorInvalidValue
                                        message:@"keycloak must be an object"];
        return nil;
    }
    NSString *issuer = keycloak[@"issuer"];
    NSString *jwksUrl = keycloak[@"jwks_url"];
    NSString *audience = keycloak[@"audience"];
    if (![issuer isKindOfClass:[NSString class]] || issuer.length == 0) {
        if (error) *error = [self errorWithCode:TTIOWBConfigErrorMissingField
                                        message:@"keycloak.issuer is required"];
        return nil;
    }
    if (![jwksUrl isKindOfClass:[NSString class]] || jwksUrl.length == 0) {
        if (error) *error = [self errorWithCode:TTIOWBConfigErrorMissingField
                                        message:@"keycloak.jwks_url is required"];
        return nil;
    }
    if (![audience isKindOfClass:[NSString class]] || audience.length == 0) {
        if (error) *error = [self errorWithCode:TTIOWBConfigErrorMissingField
                                        message:@"keycloak.audience is required"];
        return nil;
    }
    NSArray *algs = keycloak[@"allowed_algs"];
    if (algs && ![algs isKindOfClass:[NSArray class]]) {
        if (error) *error = [self errorWithCode:TTIOWBConfigErrorInvalidValue
                                        message:@"keycloak.allowed_algs must be an array"];
        return nil;
    }
    cfg->_keycloakEnabled = YES;
    cfg->_keycloakIssuer = [issuer copy];
    cfg->_keycloakJwksUrl = [jwksUrl copy];
    cfg->_keycloakAudience = [audience copy];
    cfg->_keycloakAllowedAlgs = [(algs ?: @[@"RS256"]) copy];
}
```

> Declare the matching ivars if the class uses explicit ivars; otherwise `@synthesize`/auto-synthesis with the `cfg->_field` pattern already used by `key_custody` applies. Match the file's existing style exactly.

- [ ] **Step 5: Run — expect PASS**

Run: `bash scripts/build.sh check 2>&1 | grep -E "SP2 keycloak config|FAIL"`
Expected: the 3 tests pass, no FAIL.

- [ ] **Step 6: Commit**

```bash
git add Source/Core/TTIOWBConfig.h Source/Core/TTIOWBConfig.m Tests/TtioWBTests.m
git commit -m "feat(workbench): keycloak config block for SP2 JWT auth"
```

---

## Task 2: base64url decode/encode utility

**Files:**
- Create: `Source/Auth/TTIOWBBase64Url.h`, `Source/Auth/TTIOWBBase64Url.m`
- Modify: `Source/GNUmakefile` (add the header + .m)
- Test: `Tests/TtioWBTests.m`

- [ ] **Step 1: Write the failing test**

```objc
static void test_base64url_roundtrip_and_decode(void) {
    // "Hello" -> base64url "SGVsbG8" (no padding)
    NSData *d = [@"Hello" dataUsingEncoding:NSUTF8StringEncoding];
    NSString *enc = TTIOWBBase64UrlEncode(d);
    PASS([enc isEqualToString:@"SGVsbG8"], "encode no-pad (%@)", enc);
    NSData *back = TTIOWBBase64UrlDecode(@"SGVsbG8");
    PASS([back isEqualToString:d], "decode no-pad");
    // url-safe chars: 0xFB 0xFF -> "-_8" ... assert it handles - and _
    NSData *raw = [[NSData alloc] initWithBytes:(const uint8_t[]){0xFB,0xFF,0xBF} length:3];
    NSString *u = TTIOWBBase64UrlEncode(raw);
    PASS([u rangeOfString:@"+"].location == NSNotFound &&
         [u rangeOfString:@"/"].location == NSNotFound, "url-safe alphabet");
    PASS([TTIOWBBase64UrlDecode(u) isEqualToData:raw], "decode url-safe");
    PASS(TTIOWBBase64UrlDecode(@"!!!not!!!") == nil, "invalid -> nil");
}
```
(Use `isEqualToData:`; the `isEqualToString:` in the first assert should be `isEqualToData:` — match data comparison.)

- [ ] **Step 2: Run — expect FAIL** (functions undefined)

- [ ] **Step 3: Implement `TTIOWBBase64Url.h`**

```objc
#import <Foundation/Foundation.h>
NS_ASSUME_NONNULL_BEGIN
/// base64url (RFC 7515) without padding. Returns nil on invalid input.
NSData * _Nullable TTIOWBBase64UrlDecode(NSString *s);
NSString *TTIOWBBase64UrlEncode(NSData *data);
NS_ASSUME_NONNULL_END
```

- [ ] **Step 4: Implement `TTIOWBBase64Url.m`**

```objc
#import "TTIOWBBase64Url.h"

NSString *TTIOWBBase64UrlEncode(NSData *data) {
    NSString *b64 = [data base64EncodedStringWithOptions:0];
    b64 = [b64 stringByReplacingOccurrencesOfString:@"+" withString:@"-"];
    b64 = [b64 stringByReplacingOccurrencesOfString:@"/" withString:@"_"];
    return [b64 stringByReplacingOccurrencesOfString:@"=" withString:@""];
}

NSData *TTIOWBBase64UrlDecode(NSString *s) {
    if (s.length == 0) return nil;
    NSMutableString *b64 = [[s mutableCopy] autorelease] ?: [s mutableCopy];
    [b64 replaceOccurrencesOfString:@"-" withString:@"+"
                            options:0 range:NSMakeRange(0, b64.length)];
    [b64 replaceOccurrencesOfString:@"_" withString:@"/"
                            options:0 range:NSMakeRange(0, b64.length)];
    while (b64.length % 4 != 0) [b64 appendString:@"="];
    return [[NSData alloc] initWithBase64EncodedString:b64
                                              options:0]; // nil on invalid
}
```
> The library target is ARC, so drop the manual `autorelease`/`mutableCopy` dance — use `NSMutableString *b64 = [s mutableCopy];` plainly. (The `?: [s mutableCopy]` is only a guard against a hypothetical nil; remove it.) Final ARC form:
> ```objc
> NSData *TTIOWBBase64UrlDecode(NSString *s) {
>     if (s.length == 0) return nil;
>     NSMutableString *b64 = [s mutableCopy];
>     [b64 replaceOccurrencesOfString:@"-" withString:@"+" options:0 range:NSMakeRange(0,b64.length)];
>     [b64 replaceOccurrencesOfString:@"_" withString:@"/" options:0 range:NSMakeRange(0,b64.length)];
>     while (b64.length % 4 != 0) [b64 appendString:@"="];
>     return [[NSData alloc] initWithBase64EncodedString:b64 options:0];
> }
> ```

- [ ] **Step 5: Register in `Source/GNUmakefile`** — add `Auth/TTIOWBBase64Url.h` to `libTtioWB_HEADER_FILES` and `Auth/TTIOWBBase64Url.m` to `libTtioWB_OBJC_FILES`.

- [ ] **Step 6: Run — expect PASS**

Run: `bash scripts/build.sh check 2>&1 | grep -E "base64url|FAIL"`

- [ ] **Step 7: Commit**

```bash
git add Source/Auth/TTIOWBBase64Url.h Source/Auth/TTIOWBBase64Url.m Source/GNUmakefile Tests/TtioWBTests.m
git commit -m "feat(workbench): base64url codec for JWT decoding"
```

---

## Task 3: JWKS → EVP_PKEY key construction

**Files:**
- Create: `Source/Auth/TTIOWBJwks.h`, `Source/Auth/TTIOWBJwks.m`
- Modify: `Source/GNUmakefile`
- Test: `Tests/TtioWBTests.m`

`TTIOWBJwks` parses a JWKS JSON document into a `kid → EVP_PKEY*` map and exposes the `TTIOWBJwksSource` protocol. Keys are built with OpenSSL 3 `EVP_PKEY_fromdata`. The `EVP_PKEY*` is returned as `void*` to keep OpenSSL out of the public header (cast in the verifier).

- [ ] **Step 1: Write the failing test** (build a JWKS from a generated RSA key, assert a key comes back for its kid)

```objc
// helper available after this task's impl: TTIOWBTestMakeRsaJwks(kid, &pkeyOut) -> NSData* jwks json
static void test_jwks_parses_rsa_key(void) {
    NSString *jwks = @"{\"keys\":[{\"kty\":\"RSA\",\"kid\":\"k1\",\"alg\":\"RS256\",\"use\":\"sig\","
        "\"n\":\"<nb64url>\",\"e\":\"AQAB\"}]}"; // built dynamically in real test, see Task 5 helper
    NSError *e = nil;
    TTIOWBJwks *j = [TTIOWBJwks jwksFromData:[jwks dataUsingEncoding:NSUTF8StringEncoding] error:&e];
    PASS(j != nil, "jwks parses");
    PASS([j evpKeyForKid:@"k1" error:NULL] != NULL, "key for known kid");
    PASS([j evpKeyForKid:@"nope" error:NULL] == NULL, "nil for unknown kid");
}
```
> The literal `n` above is a placeholder; the real test builds the JWKS from a freshly generated key using the helper introduced in Task 5 (`TTIOWBTestBuildRsaJwks`). Until then, this test is wired but uses the helper — so **implement the Task 5 test helper's `…BuildRsaJwks` first if executing strictly TDD**, or generate the modulus inline here. Simplest: generate the key in the test with `EVP_RSA_gen(2048)`, extract `n`/`e` via `EVP_PKEY_get_bn_param`, base64url them, assemble the JSON. Include that inline in this test.

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `TTIOWBJwks.h`**

```objc
#import <Foundation/Foundation.h>
NS_ASSUME_NONNULL_BEGIN

@protocol TTIOWBJwksSource <NSObject>
/// Returns an OpenSSL `EVP_PKEY*` (as void*) for the given kid, or NULL if unknown.
- (void * _Nullable)evpKeyForKid:(NSString *)kid error:(NSError * _Nullable * _Nullable)error;
@end

@interface TTIOWBJwks : NSObject <TTIOWBJwksSource>
+ (nullable instancetype)jwksFromData:(NSData *)data error:(NSError * _Nullable * _Nullable)error;
@end

NS_ASSUME_NONNULL_END
```

- [ ] **Step 4: Implement `TTIOWBJwks.m`** (RSA + EC via `EVP_PKEY_fromdata`)

```objc
#import "TTIOWBJwks.h"
#import "TTIOWBBase64Url.h"
#include <openssl/evp.h>
#include <openssl/param_build.h>
#include <openssl/core_names.h>
#include <openssl/bn.h>

@implementation TTIOWBJwks {
    NSMutableDictionary<NSString *, NSValue *> *_keysByKid; // kid -> EVP_PKEY* boxed
}

+ (instancetype)jwksFromData:(NSData *)data error:(NSError **)error {
    id parsed = [NSJSONSerialization JSONObjectWithData:data options:0 error:error];
    if (![parsed isKindOfClass:[NSDictionary class]]) return nil;
    NSArray *keys = parsed[@"keys"];
    if (![keys isKindOfClass:[NSArray class]]) return nil;
    TTIOWBJwks *self_ = [[TTIOWBJwks alloc] init];
    self_->_keysByKid = [NSMutableDictionary dictionary];
    for (id k in keys) {
        if (![k isKindOfClass:[NSDictionary class]]) continue;
        NSString *kid = k[@"kid"]; NSString *kty = k[@"kty"];
        if (![kid isKindOfClass:[NSString class]] || ![kty isKindOfClass:[NSString class]]) continue;
        EVP_PKEY *pkey = NULL;
        if ([kty isEqualToString:@"RSA"]) pkey = buildRSA(k[@"n"], k[@"e"]);
        else if ([kty isEqualToString:@"EC"]) pkey = buildEC(k[@"crv"], k[@"x"], k[@"y"]);
        if (pkey) _keysByKid[kid] = [NSValue valueWithPointer:pkey];
    }
    return self_;
}

static EVP_PKEY *buildRSA(NSString *nB64, NSString *eB64) {
    if (![nB64 isKindOfClass:[NSString class]] || ![eB64 isKindOfClass:[NSString class]]) return NULL;
    NSData *nD = TTIOWBBase64UrlDecode(nB64), *eD = TTIOWBBase64UrlDecode(eB64);
    if (!nD || !eD) return NULL;
    BIGNUM *n = BN_bin2bn(nD.bytes, (int)nD.length, NULL);
    BIGNUM *e = BN_bin2bn(eD.bytes, (int)eD.length, NULL);
    EVP_PKEY *pkey = NULL; OSSL_PARAM_BLD *bld = OSSL_PARAM_BLD_new();
    if (n && e && bld &&
        OSSL_PARAM_BLD_push_BN(bld, OSSL_PKEY_PARAM_RSA_N, n) &&
        OSSL_PARAM_BLD_push_BN(bld, OSSL_PKEY_PARAM_RSA_E, e)) {
        OSSL_PARAM *params = OSSL_PARAM_BLD_to_param(bld);
        EVP_PKEY_CTX *ctx = EVP_PKEY_CTX_new_from_name(NULL, "RSA", NULL);
        if (ctx && params && EVP_PKEY_fromdata_init(ctx) > 0)
            EVP_PKEY_fromdata(ctx, &pkey, EVP_PKEY_PUBLIC_KEY, params);
        OSSL_PARAM_free(params); EVP_PKEY_CTX_free(ctx);
    }
    OSSL_PARAM_BLD_free(bld); BN_free(n); BN_free(e);
    return pkey;
}

static EVP_PKEY *buildEC(NSString *crv, NSString *xB64, NSString *yB64) {
    if (![crv isEqualToString:@"P-256"]) return NULL; // ES256 only
    NSData *xD = TTIOWBBase64UrlDecode(xB64), *yD = TTIOWBBase64UrlDecode(yB64);
    if (!xD || !yD || xD.length != 32 || yD.length != 32) return NULL;
    uint8_t pub[65]; pub[0] = 0x04; memcpy(pub+1, xD.bytes, 32); memcpy(pub+33, yD.bytes, 32);
    EVP_PKEY *pkey = NULL; OSSL_PARAM_BLD *bld = OSSL_PARAM_BLD_new();
    if (bld &&
        OSSL_PARAM_BLD_push_utf8_string(bld, OSSL_PKEY_PARAM_GROUP_NAME, "prime256v1", 0) &&
        OSSL_PARAM_BLD_push_octet_string(bld, OSSL_PKEY_PARAM_PUB_KEY, pub, sizeof(pub))) {
        OSSL_PARAM *params = OSSL_PARAM_BLD_to_param(bld);
        EVP_PKEY_CTX *ctx = EVP_PKEY_CTX_new_from_name(NULL, "EC", NULL);
        if (ctx && params && EVP_PKEY_fromdata_init(ctx) > 0)
            EVP_PKEY_fromdata(ctx, &pkey, EVP_PKEY_PUBLIC_KEY, params);
        OSSL_PARAM_free(params); EVP_PKEY_CTX_free(ctx);
    }
    OSSL_PARAM_BLD_free(bld);
    return pkey;
}

- (void *)evpKeyForKid:(NSString *)kid error:(NSError **)error {
    NSValue *v = _keysByKid[kid];
    return v ? [v pointerValue] : NULL;
}

- (void)dealloc {
    for (NSValue *v in _keysByKid.allValues) EVP_PKEY_free((EVP_PKEY *)[v pointerValue]);
}
@end
```

- [ ] **Step 5: Register in `Source/GNUmakefile`** (header + .m).

- [ ] **Step 6: Run — expect PASS**

Run: `bash scripts/build.sh check 2>&1 | grep -E "jwks_parses|FAIL"`

- [ ] **Step 7: Commit**

```bash
git add Source/Auth/TTIOWBJwks.h Source/Auth/TTIOWBJwks.m Source/GNUmakefile Tests/TtioWBTests.m
git commit -m "feat(workbench): JWKS parsing + EVP_PKEY construction (RSA/EC)"
```

---

## Task 4: claim validation (pure) + `TTIOWBJwtClaims`

**Files:**
- Create: `Source/Auth/TTIOWBJwtClaims.h`, `Source/Auth/TTIOWBJwtClaims.m`
- Modify: `Source/GNUmakefile`
- Test: `Tests/TtioWBTests.m`

`TTIOWBJwtClaims` holds parsed claims; a pure function validates them against the config (issuer, audience, clock skew) given the current time, so the time-sensitive logic is testable without signing.

- [ ] **Step 1: Write the failing tests**

```objc
static TTIOWBJwtClaims *mkClaims(NSString *iss, NSArray *aud, long long exp, long long nbf) {
    TTIOWBJwtClaims *c = [TTIOWBJwtClaims new];
    c.issuer = iss; c.audiences = aud; c.expiresAt = exp; c.notBefore = nbf;
    c.subject = @"01HZESACCOUNTULID0000000000"; c.scope = @"ttio.connector";
    return c;
}
static void test_claims_valid_accepted(void) {
    long long now = 1000000;
    TTIOWBJwtClaims *c = mkClaims(@"https://kc/realms/ttio", @[@"tti-workbench"], now+300, now-10);
    NSError *e = nil;
    BOOL ok = TTIOWBJwtClaimsValid(c, @"https://kc/realms/ttio", @"tti-workbench", now, 30, &e);
    PASS(ok, "valid claims accepted (%@)", e);
}
static void test_claims_expired_rejected(void) {
    long long now = 1000000;
    TTIOWBJwtClaims *c = mkClaims(@"https://kc/realms/ttio", @[@"tti-workbench"], now-60, now-300);
    PASS(!TTIOWBJwtClaimsValid(c, @"https://kc/realms/ttio", @"tti-workbench", now, 30, NULL), "expired rejected");
}
static void test_claims_wrong_aud_rejected(void) {
    long long now = 1000000;
    TTIOWBJwtClaims *c = mkClaims(@"https://kc/realms/ttio", @[@"ttio-mcp"], now+300, now-10);
    PASS(!TTIOWBJwtClaimsValid(c, @"https://kc/realms/ttio", @"tti-workbench", now, 30, NULL), "wrong aud rejected");
}
static void test_claims_wrong_iss_rejected(void) {
    long long now = 1000000;
    TTIOWBJwtClaims *c = mkClaims(@"https://evil/realms/x", @[@"tti-workbench"], now+300, now-10);
    PASS(!TTIOWBJwtClaimsValid(c, @"https://kc/realms/ttio", @"tti-workbench", now, 30, NULL), "wrong iss rejected");
}
static void test_claims_nbf_future_rejected(void) {
    long long now = 1000000;
    TTIOWBJwtClaims *c = mkClaims(@"https://kc/realms/ttio", @[@"tti-workbench"], now+300, now+120);
    PASS(!TTIOWBJwtClaimsValid(c, @"https://kc/realms/ttio", @"tti-workbench", now, 30, NULL), "future nbf rejected");
}
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `TTIOWBJwtClaims.h`**

```objc
#import <Foundation/Foundation.h>
NS_ASSUME_NONNULL_BEGIN
@interface TTIOWBJwtClaims : NSObject
@property (nonatomic, copy, nullable) NSString *issuer;
@property (nonatomic, copy, nullable) NSString *subject;
@property (nonatomic, copy, nullable) NSString *preferredUsername;
@property (nonatomic, copy, nullable) NSArray<NSString *> *audiences;
@property (nonatomic, copy, nullable) NSString *scope;
@property (nonatomic) long long expiresAt;  // 0 = absent
@property (nonatomic) long long notBefore;  // 0 = absent
@end

/// Pure validation: issuer exact-match, audience membership, exp/nbf with skew (seconds).
BOOL TTIOWBJwtClaimsValid(TTIOWBJwtClaims *c, NSString *expectedIssuer,
                         NSString *expectedAudience, long long now,
                         long long skewSeconds, NSError * _Nullable * _Nullable error);
NS_ASSUME_NONNULL_END
```

- [ ] **Step 4: Implement `TTIOWBJwtClaims.m`**

```objc
#import "TTIOWBJwtClaims.h"
static NSError *err(NSString *m) {
    return [NSError errorWithDomain:@"TTIOWBJwt" code:1 userInfo:@{NSLocalizedDescriptionKey:m}];
}
@implementation TTIOWBJwtClaims @end

BOOL TTIOWBJwtClaimsValid(TTIOWBJwtClaims *c, NSString *expIss, NSString *expAud,
                         long long now, long long skew, NSError **error) {
    if (![c.issuer isEqualToString:expIss]) { if (error) *error = err(@"issuer mismatch"); return NO; }
    if (![c.audiences containsObject:expAud]) { if (error) *error = err(@"audience mismatch"); return NO; }
    if (c.expiresAt != 0 && now > c.expiresAt + skew) { if (error) *error = err(@"token expired"); return NO; }
    if (c.notBefore != 0 && now + skew < c.notBefore) { if (error) *error = err(@"token not yet valid"); return NO; }
    return YES;
}
```

- [ ] **Step 5: Register in `Source/GNUmakefile`; run — expect PASS**

Run: `bash scripts/build.sh check 2>&1 | grep -E "claims_|FAIL"`

- [ ] **Step 6: Commit**

```bash
git add Source/Auth/TTIOWBJwtClaims.h Source/Auth/TTIOWBJwtClaims.m Source/GNUmakefile Tests/TtioWBTests.m
git commit -m "feat(workbench): JWT claim validation (iss/aud/exp/nbf)"
```

---

## Task 5: JWT verifier (parse + signature verify) + test signing helper

**Files:**
- Create: `Source/Auth/TTIOWBJwtVerifier.h`, `Source/Auth/TTIOWBJwtVerifier.m`
- Modify: `Source/GNUmakefile`
- Test: `Tests/TtioWBTests.m` (add an in-test JWT signer + JWKS builder helper)

`TTIOWBJwtVerifier` ties everything: split the JWT, decode header (`alg`, `kid`), enforce the alg whitelist (reject `none`/unlisted), fetch the key from the `id<TTIOWBJwksSource>`, verify the signature over `header.payload` (RS256 directly; ES256 by converting raw `r‖s` to DER), decode the payload into `TTIOWBJwtClaims`, then run `TTIOWBJwtClaimsValid`.

- [ ] **Step 1: Write the test signing helper** (RSA: generate a key, build a one-key JWKS, sign a JWT)

```objc
// Returns a signed compact JWT; fills *jwksOut with a JWKS JSON containing the public key under `kid`.
static NSString *TTIOWBTestSignRS256(NSDictionary *claims, NSString *kid, NSData **jwksOut) {
    EVP_PKEY *pkey = EVP_RSA_gen(2048);
    // header + payload
    NSDictionary *hdr = @{@"alg":@"RS256", @"typ":@"JWT", @"kid":kid};
    NSData *hD = [NSJSONSerialization dataWithJSONObject:hdr options:0 error:NULL];
    NSData *pD = [NSJSONSerialization dataWithJSONObject:claims options:0 error:NULL];
    NSString *signingInput = [NSString stringWithFormat:@"%@.%@",
        TTIOWBBase64UrlEncode(hD), TTIOWBBase64UrlEncode(pD)];
    NSData *si = [signingInput dataUsingEncoding:NSUTF8StringEncoding];
    // sign
    uint8_t sig[512]; size_t siglen = sizeof(sig);
    EVP_MD_CTX *ctx = EVP_MD_CTX_new();
    EVP_DigestSignInit(ctx, NULL, EVP_sha256(), NULL, pkey);
    EVP_DigestSign(ctx, sig, &siglen, si.bytes, si.length);
    EVP_MD_CTX_free(ctx);
    NSString *jwt = [NSString stringWithFormat:@"%@.%@", signingInput,
        TTIOWBBase64UrlEncode([NSData dataWithBytes:sig length:siglen])];
    // export public n/e -> JWKS
    BIGNUM *n=NULL,*e=NULL;
    EVP_PKEY_get_bn_param(pkey, OSSL_PKEY_PARAM_RSA_N, &n);
    EVP_PKEY_get_bn_param(pkey, OSSL_PKEY_PARAM_RSA_E, &e);
    uint8_t nb[512], eb[16]; int nl = BN_bn2bin(n, nb), el = BN_bn2bin(e, eb);
    NSString *nB = TTIOWBBase64UrlEncode([NSData dataWithBytes:nb length:nl]);
    NSString *eB = TTIOWBBase64UrlEncode([NSData dataWithBytes:eb length:el]);
    NSDictionary *jwks = @{@"keys":@[@{@"kty":@"RSA",@"kid":kid,@"alg":@"RS256",@"n":nB,@"e":eB}]};
    *jwksOut = [NSJSONSerialization dataWithJSONObject:jwks options:0 error:NULL];
    BN_free(n); BN_free(e); EVP_PKEY_free(pkey);
    return jwt;
}
```

- [ ] **Step 2: Write the failing tests** (accept valid; reject expired/wrong-aud/bad-sig/alg=none/unknown-kid)

```objc
static void test_verifier_accepts_valid(void) {
    long long now = (long long)time(NULL);
    NSDictionary *claims = @{@"iss":@"https://kc/realms/ttio", @"aud":@"tti-workbench",
        @"sub":@"01HZACCT", @"preferred_username":@"alice",
        @"exp":@(now+300), @"nbf":@(now-10), @"scope":@"ttio.connector"};
    NSData *jwks=nil; NSString *jwt = TTIOWBTestSignRS256(claims, @"k1", &jwks);
    TTIOWBJwks *src = [TTIOWBJwks jwksFromData:jwks error:NULL];
    TTIOWBJwtVerifier *v = [[TTIOWBJwtVerifier alloc]
        initWithJwksSource:src issuer:@"https://kc/realms/ttio"
                  audience:@"tti-workbench" allowedAlgs:@[@"RS256"] skewSeconds:30];
    NSError *e=nil; TTIOWBJwtClaims *out = [v validateToken:jwt error:&e];
    PASS(out != nil, "valid JWT accepted (%@)", e);
    PASS([out.subject isEqualToString:@"01HZACCT"], "sub extracted");
    PASS([out.preferredUsername isEqualToString:@"alice"], "preferred_username extracted");
}
static void test_verifier_rejects_expired(void) {
    long long now=(long long)time(NULL);
    NSData *jwks=nil; NSString *jwt = TTIOWBTestSignRS256(
        @{@"iss":@"https://kc/realms/ttio",@"aud":@"tti-workbench",@"sub":@"x",@"exp":@(now-100)}, @"k1", &jwks);
    TTIOWBJwtVerifier *v=[[TTIOWBJwtVerifier alloc] initWithJwksSource:[TTIOWBJwks jwksFromData:jwks error:NULL]
        issuer:@"https://kc/realms/ttio" audience:@"tti-workbench" allowedAlgs:@[@"RS256"] skewSeconds:30];
    PASS([v validateToken:jwt error:NULL] == nil, "expired rejected");
}
static void test_verifier_rejects_wrong_aud(void) {
    long long now=(long long)time(NULL);
    NSData *jwks=nil; NSString *jwt = TTIOWBTestSignRS256(
        @{@"iss":@"https://kc/realms/ttio",@"aud":@"ttio-mcp",@"sub":@"x",@"exp":@(now+300)}, @"k1", &jwks);
    TTIOWBJwtVerifier *v=[[TTIOWBJwtVerifier alloc] initWithJwksSource:[TTIOWBJwks jwksFromData:jwks error:NULL]
        issuer:@"https://kc/realms/ttio" audience:@"tti-workbench" allowedAlgs:@[@"RS256"] skewSeconds:30];
    PASS([v validateToken:jwt error:NULL] == nil, "wrong aud rejected");
}
static void test_verifier_rejects_bad_signature(void) {
    long long now=(long long)time(NULL);
    NSData *jwks=nil; NSString *jwt = TTIOWBTestSignRS256(
        @{@"iss":@"https://kc/realms/ttio",@"aud":@"tti-workbench",@"sub":@"x",@"exp":@(now+300)}, @"k1", &jwks);
    // tamper: flip last char of signature
    NSString *bad = [[jwt substringToIndex:jwt.length-1] stringByAppendingString:
        ([jwt hasSuffix:@"A"] ? @"B" : @"A")];
    TTIOWBJwtVerifier *v=[[TTIOWBJwtVerifier alloc] initWithJwksSource:[TTIOWBJwks jwksFromData:jwks error:NULL]
        issuer:@"https://kc/realms/ttio" audience:@"tti-workbench" allowedAlgs:@[@"RS256"] skewSeconds:30];
    PASS([v validateToken:bad error:NULL] == nil, "bad signature rejected");
}
static void test_verifier_rejects_alg_none(void) {
    NSDictionary *hdr=@{@"alg":@"none",@"typ":@"JWT"};
    NSDictionary *pl=@{@"iss":@"https://kc/realms/ttio",@"aud":@"tti-workbench",@"sub":@"x"};
    NSString *jwt=[NSString stringWithFormat:@"%@.%@.",
        TTIOWBBase64UrlEncode([NSJSONSerialization dataWithJSONObject:hdr options:0 error:NULL]),
        TTIOWBBase64UrlEncode([NSJSONSerialization dataWithJSONObject:pl options:0 error:NULL])];
    NSData *jwks=nil; (void)TTIOWBTestSignRS256(@{@"sub":@"x"}, @"k1", &jwks);
    TTIOWBJwtVerifier *v=[[TTIOWBJwtVerifier alloc] initWithJwksSource:[TTIOWBJwks jwksFromData:jwks error:NULL]
        issuer:@"https://kc/realms/ttio" audience:@"tti-workbench" allowedAlgs:@[@"RS256"] skewSeconds:30];
    PASS([v validateToken:jwt error:NULL] == nil, "alg=none rejected");
}
static void test_verifier_rejects_unknown_kid(void) {
    long long now=(long long)time(NULL);
    NSData *jwks=nil; NSString *jwt = TTIOWBTestSignRS256(
        @{@"iss":@"https://kc/realms/ttio",@"aud":@"tti-workbench",@"sub":@"x",@"exp":@(now+300)}, @"OTHERKID", &jwks);
    // build a JWKS that has a DIFFERENT kid by signing a throwaway with k1
    NSData *otherJwks=nil; (void)TTIOWBTestSignRS256(@{@"sub":@"y"}, @"k1", &otherJwks);
    TTIOWBJwtVerifier *v=[[TTIOWBJwtVerifier alloc] initWithJwksSource:[TTIOWBJwks jwksFromData:otherJwks error:NULL]
        issuer:@"https://kc/realms/ttio" audience:@"tti-workbench" allowedAlgs:@[@"RS256"] skewSeconds:30];
    PASS([v validateToken:jwt error:NULL] == nil, "unknown kid rejected");
}
```

- [ ] **Step 3: Run — expect FAIL**

- [ ] **Step 4: Implement `TTIOWBJwtVerifier.h`**

```objc
#import <Foundation/Foundation.h>
#import "TTIOWBJwks.h"
#import "TTIOWBJwtClaims.h"
NS_ASSUME_NONNULL_BEGIN
@interface TTIOWBJwtVerifier : NSObject
- (instancetype)initWithJwksSource:(id<TTIOWBJwksSource>)source
                            issuer:(NSString *)issuer
                          audience:(NSString *)audience
                       allowedAlgs:(NSArray<NSString *> *)allowedAlgs
                       skewSeconds:(long long)skewSeconds;
/// Returns validated claims or nil+error. Never logs the token.
- (nullable TTIOWBJwtClaims *)validateToken:(NSString *)jwt error:(NSError * _Nullable * _Nullable)error;
/// YES if the string looks like a JWT (three base64url segments). Used by the dispatch site.
+ (BOOL)looksLikeJwt:(NSString *)s;
@end
NS_ASSUME_NONNULL_END
```

- [ ] **Step 5: Implement `TTIOWBJwtVerifier.m`**

```objc
#import "TTIOWBJwtVerifier.h"
#import "TTIOWBBase64Url.h"
#include <openssl/evp.h>
#include <openssl/ecdsa.h>
#include <openssl/bn.h>

@implementation TTIOWBJwtVerifier {
    id<TTIOWBJwksSource> _source; NSString *_issuer, *_audience;
    NSArray<NSString *> *_algs; long long _skew;
}
- (instancetype)initWithJwksSource:(id<TTIOWBJwksSource>)source issuer:(NSString *)issuer
        audience:(NSString *)audience allowedAlgs:(NSArray<NSString *> *)allowedAlgs
        skewSeconds:(long long)skewSeconds {
    if ((self = [super init])) {
        _source = source; _issuer = [issuer copy]; _audience = [audience copy];
        _algs = [allowedAlgs copy]; _skew = skewSeconds;
    }
    return self;
}

+ (BOOL)looksLikeJwt:(NSString *)s {
    if (![s hasPrefix:@"eyJ"]) return NO;            // base64url of '{"'
    NSArray *parts = [s componentsSeparatedByString:@"."];
    return parts.count == 3;
}

static NSError *vErr(NSString *m) {
    return [NSError errorWithDomain:@"TTIOWBJwt" code:2 userInfo:@{NSLocalizedDescriptionKey:m}];
}

// Convert raw r||s (64 bytes for P-256) into DER ECDSA-Sig.
static NSData *ecRawToDer(NSData *raw) {
    if (raw.length != 64) return nil;
    ECDSA_SIG *sig = ECDSA_SIG_new();
    BIGNUM *r = BN_bin2bn(raw.bytes, 32, NULL);
    BIGNUM *s = BN_bin2bn((const uint8_t *)raw.bytes + 32, 32, NULL);
    ECDSA_SIG_set0(sig, r, s);
    uint8_t *der = NULL; int derlen = i2d_ECDSA_SIG(sig, &der);
    NSData *out = derlen > 0 ? [NSData dataWithBytes:der length:derlen] : nil;
    OPENSSL_free(der); ECDSA_SIG_free(sig);
    return out;
}

- (TTIOWBJwtClaims *)validateToken:(NSString *)jwt error:(NSError **)error {
    NSArray<NSString *> *parts = [jwt componentsSeparatedByString:@"."];
    if (parts.count != 3) { if (error) *error = vErr(@"malformed jwt"); return nil; }
    NSData *hD = TTIOWBBase64UrlDecode(parts[0]);
    NSData *pD = TTIOWBBase64UrlDecode(parts[1]);
    NSData *sD = TTIOWBBase64UrlDecode(parts[2]);
    if (!hD || !pD || !sD) { if (error) *error = vErr(@"bad base64url"); return nil; }
    id hdr = [NSJSONSerialization JSONObjectWithData:hD options:0 error:NULL];
    id pl  = [NSJSONSerialization JSONObjectWithData:pD options:0 error:NULL];
    if (![hdr isKindOfClass:[NSDictionary class]] || ![pl isKindOfClass:[NSDictionary class]]) {
        if (error) *error = vErr(@"bad json"); return nil;
    }
    NSString *alg = hdr[@"alg"], *kid = hdr[@"kid"];
    if (![alg isKindOfClass:[NSString class]] || ![_algs containsObject:alg]) {
        if (error) *error = vErr(@"alg not allowed"); return nil;   // rejects "none" & unlisted
    }
    if (![kid isKindOfClass:[NSString class]]) { if (error) *error = vErr(@"missing kid"); return nil; }
    EVP_PKEY *pkey = (EVP_PKEY *)[_source evpKeyForKid:kid error:NULL];
    if (!pkey) { if (error) *error = vErr(@"unknown kid"); return nil; }

    NSString *signingInput = [NSString stringWithFormat:@"%@.%@", parts[0], parts[1]];
    NSData *si = [signingInput dataUsingEncoding:NSUTF8StringEncoding];
    NSData *sigDer = [alg isEqualToString:@"ES256"] ? ecRawToDer(sD) : sD;
    if (!sigDer) { if (error) *error = vErr(@"bad signature encoding"); return nil; }

    EVP_MD_CTX *ctx = EVP_MD_CTX_new();
    int ok = 0;
    if (EVP_DigestVerifyInit(ctx, NULL, EVP_sha256(), NULL, pkey) == 1 &&
        EVP_DigestVerifyUpdate(ctx, si.bytes, si.length) == 1) {
        ok = EVP_DigestVerifyFinal(ctx, sigDer.bytes, sigDer.length);
    }
    EVP_MD_CTX_free(ctx);
    if (ok != 1) { if (error) *error = vErr(@"signature verification failed"); return nil; }

    TTIOWBJwtClaims *c = [TTIOWBJwtClaims new];
    c.issuer = pl[@"iss"]; c.subject = pl[@"sub"];
    c.preferredUsername = pl[@"preferred_username"]; c.scope = pl[@"scope"];
    id aud = pl[@"aud"];
    c.audiences = [aud isKindOfClass:[NSArray class]] ? aud
                : ([aud isKindOfClass:[NSString class]] ? @[aud] : @[]);
    c.expiresAt = [pl[@"exp"] isKindOfClass:[NSNumber class]] ? [pl[@"exp"] longLongValue] : 0;
    c.notBefore = [pl[@"nbf"] isKindOfClass:[NSNumber class]] ? [pl[@"nbf"] longLongValue] : 0;

    long long now = (long long)time(NULL);
    if (!TTIOWBJwtClaimsValid(c, _issuer, _audience, now, _skew, error)) return nil;
    if (c.subject.length == 0) { if (error) *error = vErr(@"missing sub"); return nil; }
    return c;
}
@end
```

- [ ] **Step 6: Register in `Source/GNUmakefile`; run — expect PASS** (all 6 verifier tests)

Run: `bash scripts/build.sh check 2>&1 | grep -E "verifier_|FAIL"`

- [ ] **Step 7: Commit**

```bash
git add Source/Auth/TTIOWBJwtVerifier.h Source/Auth/TTIOWBJwtVerifier.m Source/GNUmakefile Tests/TtioWBTests.m
git commit -m "feat(workbench): JWT verifier (RS256/ES256 sig + claim validation)"
```

> Optional ES256 coverage: add a `TTIOWBTestSignES256` helper (`EVP_EC_gen("P-256")`, sign → 64-byte raw via DER→raw, or sign and let `validateToken` do DER→raw) and a `test_verifier_accepts_valid_es256`. RS256 is the Keycloak default and the integration path; ES256 support is built but only the unit test exercises it.

---

## Task 6: JWKS cache + libcurl transport

**Files:**
- Create: `Source/Auth/TTIOWBHttpGetter.h` (protocol), `Source/Auth/TTIOWBCurlHttpGetter.{h,m}`, `Source/Auth/TTIOWBJwksCache.{h,m}`
- Modify: `Source/GNUmakefile`, `Tools/GNUmakefile`, `Tests/GNUmakefile` (add `-lcurl`)
- Test: `Tests/TtioWBTests.m`

`TTIOWBJwksCache` implements `TTIOWBJwksSource`. It lazily fetches the JWKS via an injected `id<TTIOWBHttpGetter>`, caches the parsed `TTIOWBJwks`, and **refreshes on unknown-kid** (key rotation) subject to a minimum refresh interval, plus a TTL.

- [ ] **Step 1: Write the failing test** (fake getter; cache serves keys; refreshes on unknown kid)

```objc
@interface FakeGetter : NSObject <TTIOWBHttpGetter>
@property (nonatomic, strong) NSData *payload; @property (nonatomic) int calls;
@end
@implementation FakeGetter
- (NSData *)getURL:(NSString *)url error:(NSError **)e { _calls++; return _payload; }
@end

static void test_jwks_cache_serves_and_refreshes(void) {
    NSData *jwks1=nil; (void)TTIOWBTestSignRS256(@{@"sub":@"x"}, @"k1", &jwks1);
    FakeGetter *g = [FakeGetter new]; g.payload = jwks1;
    TTIOWBJwksCache *cache = [[TTIOWBJwksCache alloc] initWithGetter:g
        url:@"https://kc/certs" ttlSeconds:300 minRefreshSeconds:0];
    PASS([cache evpKeyForKid:@"k1" error:NULL] != NULL, "fetches + serves k1");
    PASS(g.calls == 1, "one fetch");
    PASS([cache evpKeyForKid:@"k1" error:NULL] != NULL, "served from cache");
    PASS(g.calls == 1, "no refetch for known kid");
    // rotate: new JWKS has k2; unknown kid triggers a refresh
    NSData *jwks2=nil; (void)TTIOWBTestSignRS256(@{@"sub":@"y"}, @"k2", &jwks2);
    g.payload = jwks2;
    PASS([cache evpKeyForKid:@"k2" error:NULL] != NULL, "refresh-on-unknown-kid finds k2");
    PASS(g.calls == 2, "one refresh for unknown kid");
}
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `TTIOWBHttpGetter.h`**

```objc
#import <Foundation/Foundation.h>
NS_ASSUME_NONNULL_BEGIN
@protocol TTIOWBHttpGetter <NSObject>
- (nullable NSData *)getURL:(NSString *)url error:(NSError * _Nullable * _Nullable)error;
@end
NS_ASSUME_NONNULL_END
```

- [ ] **Step 4: Implement `TTIOWBCurlHttpGetter.{h,m}`** (synchronous libcurl GET, TLS verify on)

```objc
// .h
#import <Foundation/Foundation.h>
#import "TTIOWBHttpGetter.h"
@interface TTIOWBCurlHttpGetter : NSObject <TTIOWBHttpGetter>
@end

// .m
#import "TTIOWBCurlHttpGetter.h"
#include <curl/curl.h>
static size_t writeCb(char *ptr, size_t sz, size_t n, void *ud) {
    [(__bridge NSMutableData *)ud appendBytes:ptr length:sz*n]; return sz*n;
}
@implementation TTIOWBCurlHttpGetter
- (NSData *)getURL:(NSString *)url error:(NSError **)error {
    CURL *c = curl_easy_init();
    if (!c) { if (error) *error = [NSError errorWithDomain:@"TTIOWBHttp" code:1 userInfo:nil]; return nil; }
    NSMutableData *buf = [NSMutableData data];
    curl_easy_setopt(c, CURLOPT_URL, url.UTF8String);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, writeCb);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, (__bridge void *)buf);
    curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(c, CURLOPT_TIMEOUT, 10L);
    curl_easy_setopt(c, CURLOPT_SSL_VERIFYPEER, 1L);
    curl_easy_setopt(c, CURLOPT_SSL_VERIFYHOST, 2L);
    CURLcode rc = curl_easy_perform(c);
    long code = 0; curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
    curl_easy_cleanup(c);
    if (rc != CURLE_OK || code != 200) {
        if (error) *error = [NSError errorWithDomain:@"TTIOWBHttp" code:(int)code
            userInfo:@{NSLocalizedDescriptionKey: @(curl_easy_strerror(rc))}];
        return nil;
    }
    return buf;
}
@end
```

- [ ] **Step 5: Implement `TTIOWBJwksCache.{h,m}`**

```objc
// .h
#import <Foundation/Foundation.h>
#import "TTIOWBJwks.h"
#import "TTIOWBHttpGetter.h"
@interface TTIOWBJwksCache : NSObject <TTIOWBJwksSource>
- (instancetype)initWithGetter:(id<TTIOWBHttpGetter>)getter url:(NSString *)url
                    ttlSeconds:(long long)ttl minRefreshSeconds:(long long)minRefresh;
@end

// .m
#import "TTIOWBJwksCache.h"
@implementation TTIOWBJwksCache {
    id<TTIOWBHttpGetter> _getter; NSString *_url;
    long long _ttl, _minRefresh, _lastFetch; TTIOWBJwks *_jwks;
    NSObject *_lock;
}
- (instancetype)initWithGetter:(id<TTIOWBHttpGetter>)getter url:(NSString *)url
        ttlSeconds:(long long)ttl minRefreshSeconds:(long long)minRefresh {
    if ((self = [super init])) { _getter=getter; _url=[url copy]; _ttl=ttl;
        _minRefresh=minRefresh; _lastFetch=0; _lock=[NSObject new]; }
    return self;
}
- (BOOL)_refresh {
    NSError *e=nil; NSData *d=[_getter getURL:_url error:&e];
    if (!d) return NO;
    TTIOWBJwks *j=[TTIOWBJwks jwksFromData:d error:&e];
    if (!j) return NO;
    _jwks=j; _lastFetch=(long long)time(NULL); return YES;
}
- (void *)evpKeyForKid:(NSString *)kid error:(NSError **)error {
    @synchronized (_lock) {
        long long now=(long long)time(NULL);
        if (!_jwks || (_ttl>0 && now-_lastFetch>_ttl)) [self _refresh];
        void *k = _jwks ? [_jwks evpKeyForKid:kid error:NULL] : NULL;
        if (k) return k;
        // unknown kid -> possible rotation; refresh if past min interval
        if (now-_lastFetch >= _minRefresh) { if ([self _refresh]) k=[_jwks evpKeyForKid:kid error:NULL]; }
        if (!k && error) *error=[NSError errorWithDomain:@"TTIOWBJwks" code:404 userInfo:nil];
        return k;
    }
}
@end
```

- [ ] **Step 6: Add `-lcurl`** to `Source/GNUmakefile` (`libTtioWB_LIBRARIES_DEPEND_UPON`), `Tools/GNUmakefile` (`TtioWBServer_TOOL_LIBS`), `Tests/GNUmakefile` (`TtioWBTests_TOOL_LIBS`); register the 4 new files in `Source/GNUmakefile`.

- [ ] **Step 7: Run — expect PASS**

Run: `bash scripts/build.sh check 2>&1 | grep -E "jwks_cache|FAIL"`

- [ ] **Step 8: Commit**

```bash
git add Source/Auth/TTIOWBHttpGetter.h Source/Auth/TTIOWBCurlHttpGetter.* Source/Auth/TTIOWBJwksCache.* \
        Source/GNUmakefile Tools/GNUmakefile Tests/GNUmakefile Tests/TtioWBTests.m
git commit -m "feat(workbench): JWKS cache + libcurl transport with rotation refresh"
```

---

## Task 7: Wire the JWT path into `TTIOWBAuthService` + daemon bootstrap

**Files:**
- Modify: `Source/Auth/TTIOWBAuthService.h` (add `jwtVerifier` property), `Source/Auth/TTIOWBAuthService.m` (JWT branch in `-contextForRawToken:`)
- Modify: `Tools/TtioWBServer.m` (construct verifier+cache+getter from config; set on the service)
- Test: `Tests/TtioWBTests.m`

- [ ] **Step 1: Write the failing test** (service resolves a real signed JWT to a context for a seeded user)

```objc
static void test_service_jwt_resolves_to_context(void) {
    TTIOWBAuthFixture f = makeAuthFixture();
    // seed a user; capture its ULID
    NSString *totp = _seedLoginUser(f, @"alice", @"hunter2");  (void)totp;
    TTIOWBUserRecord *u = [f.store userForUsername:@"alice" error:NULL];
    long long now=(long long)time(NULL);
    NSData *jwks=nil; NSString *jwt = TTIOWBTestSignRS256(
        @{@"iss":@"https://kc/realms/ttio", @"aud":@"tti-workbench", @"sub":u.userId,
          @"preferred_username":@"alice", @"exp":@(now+300)}, @"k1", &jwks);
    f.svc.jwtVerifier = [[TTIOWBJwtVerifier alloc]
        initWithJwksSource:[TTIOWBJwks jwksFromData:jwks error:NULL]
        issuer:@"https://kc/realms/ttio" audience:@"tti-workbench"
        allowedAlgs:@[@"RS256"] skewSeconds:30];
    NSError *e=nil;
    TTIOWBAuthContext *ctx = [f.svc contextForRawToken:jwt error:&e];
    PASS(ctx != nil, "JWT resolves to context (%@)", e);
    PASS([ctx.userId isEqualToString:u.userId], "sub mapped to account");
    PASS([ctx.provider isEqualToString:@"jwt"], "provider=jwt");
}
static void test_service_jwt_unknown_sub_rejected(void) {
    TTIOWBAuthFixture f = makeAuthFixture();
    long long now=(long long)time(NULL);
    NSData *jwks=nil; NSString *jwt = TTIOWBTestSignRS256(
        @{@"iss":@"https://kc/realms/ttio",@"aud":@"tti-workbench",@"sub":@"01HNOSUCHUSER",@"exp":@(now+300)},
        @"k1", &jwks);
    f.svc.jwtVerifier = [[TTIOWBJwtVerifier alloc]
        initWithJwksSource:[TTIOWBJwks jwksFromData:jwks error:NULL]
        issuer:@"https://kc/realms/ttio" audience:@"tti-workbench" allowedAlgs:@[@"RS256"] skewSeconds:30];
    PASS([f.svc contextForRawToken:jwt error:NULL] == nil, "unknown sub -> nil");
}
static void test_service_jwt_disabled_when_no_verifier(void) {
    TTIOWBAuthFixture f = makeAuthFixture();   // no jwtVerifier set
    PASS([f.svc contextForRawToken:@"eyJ0.eyJ0.sig" error:NULL] == nil, "no verifier -> JWT path off");
}
```

- [ ] **Step 2: Run — expect FAIL** (`jwtVerifier` property unknown)

- [ ] **Step 3: Add the property to `TTIOWBAuthService.h`**

```objc
@class TTIOWBJwtVerifier;
// ...
@property (nonatomic, strong, nullable) TTIOWBJwtVerifier *jwtVerifier;
```

- [ ] **Step 4: Add the JWT branch to `TTIOWBAuthService.m -contextForRawToken:`** (import the header; insert before the final `return nil;` at ~:219)

```objc
#import "TTIOWBJwtVerifier.h"
// ... inside -contextForRawToken:error:, after the ttiowbk_ branch:
    if (self.jwtVerifier && [TTIOWBJwtVerifier looksLikeJwt:rawToken]) {
        TTIOWBJwtClaims *claims = [self.jwtVerifier validateToken:rawToken error:error];
        if (!claims) return nil;
        TTIOWBUserRecord *u = [self.store userForId:claims.subject error:error];
        if (!u || u.disabledAt > 0) return nil;
        return [self _contextForUser:u provider:@"jwt" sessionId:nil keyId:nil];
    }
    return nil;
```

- [ ] **Step 5: Wire the daemon bootstrap** in `Tools/TtioWBServer.m` — after the config + auth service are built, when `config.keycloakEnabled`:

```objc
if (config.keycloakEnabled) {
    TTIOWBCurlHttpGetter *getter = [TTIOWBCurlHttpGetter new];
    TTIOWBJwksCache *cache = [[TTIOWBJwksCache alloc] initWithGetter:getter
        url:config.keycloakJwksUrl ttlSeconds:3600 minRefreshSeconds:60];
    authService.jwtVerifier = [[TTIOWBJwtVerifier alloc] initWithJwksSource:cache
        issuer:config.keycloakIssuer audience:config.keycloakAudience
        allowedAlgs:config.keycloakAllowedAlgs skewSeconds:30];
    NSLog(@"[auth] Keycloak JWT bearer path enabled (iss=%@, aud=%@)",
          config.keycloakIssuer, config.keycloakAudience);
}
```
(Match the actual local variable names for the config/auth-service in `TtioWBServer.m`; add the `#import`s.)

- [ ] **Step 6: Run — expect PASS** (3 service tests + full suite green)

Run: `bash scripts/build.sh check 2>&1 | grep -E "service_jwt|FAIL|failed"`
Expected: the 3 tests pass; no regressions in the existing suite.

- [ ] **Step 7: Commit**

```bash
git add Source/Auth/TTIOWBAuthService.h Source/Auth/TTIOWBAuthService.m Tools/TtioWBServer.m Tests/TtioWBTests.m
git commit -m "feat(workbench): accept Keycloak JWT bearer tokens (sub->account, provider=jwt)"
```

---

## Task 8: Live integration against real Keycloak + CI

**Files:**
- Create: `keycloak/smoke_workbench_jwt.sh` (or extend `keycloak/smoke.sh`)
- Modify: `.github/workflows/<server-ci>.yml` (install `libcurl4-openssl-dev`)
- Modify: `etc/server.example.json` (+ a `keycloak` block example, commented/optional)

- [ ] **Step 1: Add CI dependency** — in the ObjC server build job, add `libcurl4-openssl-dev` to the `apt-get install` step (next to the existing dev deps). Verify the job name/file by grepping `.github/workflows/`.

- [ ] **Step 2: Add an example `keycloak` config block** to `etc/server.example.json`:

```json
"keycloak": {
  "issuer": "http://localhost:8080/realms/ttio",
  "jwks_url": "http://localhost:8080/realms/ttio/protocol/openid-connect/certs",
  "audience": "tti-workbench",
  "allowed_algs": ["RS256"]
}
```

- [ ] **Step 3: Write the live integration smoke** (`keycloak/smoke_workbench_jwt.sh`) — single shell run (Docker lives in the WSL distro):
  1. Reuse `keycloak/` to bring up Keycloak + the stub workbench-login (or the real one), import the realm.
  2. Drive the auth-code+PKCE flow (reuse `smoke_oauth.py` machinery) to get a user token, then RFC 8693 exchange to a `tti-workbench`-audience token.
  3. Start `TtioWBServer` with a conf.json whose `keycloak` block points at the running Keycloak; seed a workbench user whose ULID equals the token `sub`.
  4. `curl` a protected workbench endpoint with `Authorization: Bearer <exchanged-token>` → expect 200; repeat with a `ttio-mcp`-audience token → expect 401.
  5. Print `JWT-SMOKE OK`; tear everything down.

- [ ] **Step 4: Run the smoke** (in WSL, one invocation)

Run: `cd ~/tti-workbench-server/keycloak && bash smoke_workbench_jwt.sh`
Expected: `JWT-SMOKE OK`.

- [ ] **Step 5: Commit**

```bash
git add keycloak/smoke_workbench_jwt.sh etc/server.example.json .github/workflows/*.yml
git commit -m "test(workbench): live Keycloak JWT bearer integration smoke + CI libcurl dep"
```

> The `sub`↔account coupling is the integration's key assertion: SP1's authenticator sets `sub` to the workbench `user_id`, and SP2 maps it back via `-userForId:`. The live smoke must seed (or reuse) a workbench user whose ULID matches the token `sub` — otherwise step 4 returns 401 even with a valid signature. If aligning the ULID is impractical in the harness, assert the 401-on-wrong-aud and a separate accept using a locally-signed token whose `sub` is a seeded user (bridging Task 7's unit approach with a running daemon).

---

## Self-Review

- **Spec coverage:** new bearer mode in the auth path → Tasks 5+7; JWKS via Keycloak (fetch+cache+rotation) → Task 6; `iss`/`aud=tti-workbench`/`exp`/`nbf`/pinned algs/reject `alg=none` → Tasks 4+5; `sub`→account → Task 7; populate existing `TTIOWBAuthContext` alongside `ttiowbs_`/`ttiowbk_` → Task 7 (reuses `_contextForUser:`); unit tests over locally-signed JWTs (valid/expired/wrong-aud/wrong-iss/bad-sig/`alg=none`/`sub` mapping) → Tasks 4–7; folded into the existing ObjC suite → all tasks; integration harness → Task 8; "no tokens logged" → verifier never logs the token (only iss/aud at enable time). Config to enable it → Task 1.
- **Placeholder scan:** the only deferred specifics are Task 8's exact CI workflow filename and `TtioWBServer.m` local variable names (both "match the actual file" instructions with the grep to find them), and the Task-3 test's inline modulus (resolved by generating the key in-test). No `alg=none` path exists — the whitelist check rejects it before any key lookup. ES256 is fully implemented (Task 5) with a noted optional unit test.
- **Type consistency:** `id<TTIOWBJwksSource> -evpKeyForKid:error:` (void* EVP_PKEY) used identically in Tasks 3/5/6; `TTIOWBJwtClaims` fields (`subject`, `preferredUsername`, `audiences`, `expiresAt`, `notBefore`, `issuer`, `scope`) consistent across Tasks 4/5/7; `TTIOWBJwtVerifier initWithJwksSource:issuer:audience:allowedAlgs:skewSeconds:` and `validateToken:error:` / `looksLikeJwt:` identical in Tasks 5/7; `TTIOWBJwksCache initWithGetter:url:ttlSeconds:minRefreshSeconds:` consistent Tasks 6/7; `TTIOWBBase64UrlDecode/Encode` consistent Tasks 2/3/5; config props `keycloakEnabled/Issuer/JwksUrl/Audience/AllowedAlgs` consistent Tasks 1/7.

## Out of scope (later phases)
- DCR trusted-hosts/redirect allowlist (SP1/Phase 3), token revocation lists, observability/metrics on the JWT path, ES256 in the live integration (RS256 is Keycloak's default), and production hosting/TLS for the JWKS endpoint.
