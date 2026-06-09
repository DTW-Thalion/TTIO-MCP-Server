# HANDOFF-M8.md — TTI-O-MCP M8: MCP Conformance Suite

## Context

M1 shipped a scaffolded server that answered `initialize`. M2–M7
built up the tool surface to 13 tools. Every unit test along the way
called tool handlers **in-process** — the suite proved our logic
worked, but never proved that the server actually speaks MCP
correctly: no `initialize` handshake over stdio, no JSON-RPC envelope
on the wire, no `tools/list` shape check against what the SDK serves.

M8 closes that gap. The new conformance suite spawns the real
`ttio-mcp` subprocess and drives it with the `mcp` Python client
SDK — the same entry point a Claude Code or custom MCP client would
use. Every existing test still runs in-process (fast, granular); the
M8 tests add end-to-end validation across the full MCP wire.

The originally-planned M8 ("Conformance + publish to TestPyPI") has
been **split**. M8 covers conformance only; a separate M9 will pick
up the TestPyPI publish workflow once TTI-O ships PyPI wheels
(tracked as TTI-O M40). PyPI rejects wheels that declare git-URL
dependencies, and TTI-O is currently pulled from a git tag, so
publishing today would mean either TestPyPI-only or restructuring
the dependency.

- M1 HANDOFF: [HANDOFF.md](HANDOFF.md) — binding decisions.
- M2 HANDOFF: [HANDOFF-M2.md](HANDOFF-M2.md) — catalog surface.
- M3 HANDOFF: [HANDOFF-M3.md](HANDOFF-M3.md) — query tools.
- M4 HANDOFF: [HANDOFF-M4.md](HANDOFF-M4.md) — cloud I/O.
- M5 HANDOFF: [HANDOFF-M5.md](HANDOFF-M5.md) — keyring & encryption.
- M6 HANDOFF: [HANDOFF-M6.md](HANDOFF-M6.md) — cloud push.
- M7 HANDOFF: [HANDOFF-M7.md](HANDOFF-M7.md) — dataset signatures.

## M8 Scope

- **`tests/test_m8_conformance.py`** — four tests, all async, all
  using `mcp.client.stdio.stdio_client` + `mcp.ClientSession` against
  a freshly-spawned subprocess. The subprocess command resolution
  mirrors the existing `tests/test_initialize.py` pattern: prefer
  `shutil.which("ttio-mcp")`; fall back to
  `sys.executable -m ttio_mcp.server` (the module already has
  `__name__ == "__main__"` guarding `main()`).
- **Initialize + `list_tools` surface.** Verifies the exact set of 13
  tool names, and checks each tool's `inputSchema` is an object with
  `additionalProperties: false` and a `properties` dict. This
  catches regressions where a new tool is added without its schema
  being wired through, or where `additionalProperties` gets dropped.
- **Linear happy path (12 of 13 tools).** One subprocess, one
  `ClientSession`, one local MS fixture built via
  `tests._fixtures.build_ms_fixture`. The call sequence accumulates
  state through the catalog row: register → list → get → get_run →
  search_identifications → get_quantifications →
  get_spectrum (plaintext) → sign_file → verify_signature → reverify
  → encrypt_file → get_spectrum (with `key_id`) → decrypt_file. The
  ordering is deliberate — sign before encrypt because HMAC requires
  plaintext; reverify after sign to cover the drift-detection happy
  path (catalog hashes get refreshed as part of `sign_file`, so
  `drift=false` is the expected outcome).
- **`ttio_push_file` (13th tool).** A separate test reuses the
  shared `moto_s3_server` session fixture from `tests/_cloud.py`
  (skipped when `moto.server`, `flask`, or `s3fs` aren't
  installed). The subprocess receives `TTIO_MCP_FSSPEC_KWARGS` with
  the moto endpoint URL and fake credentials so it can write to the
  bucket without real AWS access.
- **Error envelope on the wire.** One negative-path test calls
  `ttio_get_file` with a bogus `id` and parses the TextContent body;
  asserts `{"ok": false, "error": {"code": "not_found", "message":
  "...999..."}}`. This nails down the serialised error shape — in-
  process tests catch exceptions, not JSON strings.

## Out of Scope for M8

- **TestPyPI publish workflow.** Split to M9 per scope discussion
  above.
- **MCP-over-HTTP / SSE conformance.** The server speaks stdio only;
  if we add another transport later, that gets its own conformance
  test, not a rewrite of the stdio suite.
- **Exhaustive per-tool schema validation.** M8 checks each schema
  is a valid object with `additionalProperties: false`. Deeper
  validation (every property type is one of a known set, every
  error code from `docs/tools.md` is actually emitted somewhere,
  etc.) would duplicate the per-tool unit tests without adding
  coverage. If we ever want to publish the schemas as part of an
  external contract, a one-shot JSON Schema meta-validator is a
  cleaner place for that work than the conformance run.
- **Protocol-version negotiation matrix.** The `mcp` SDK picks the
  protocol version for us; we don't drive a client that deliberately
  negotiates a non-current version. If MCP introduces breaking
  protocol shifts, that's a compatibility concern for the SDK bump
  itself.
- **Load testing.** Zero tool-calls-per-second assertions; the
  conformance suite is correctness-only.
- **Multi-client stress.** The server is single-client by design
  (stdio). No reason to assert anything about parallel clients.

## Package Layout (new/changed in M8)

```
tests/
└── test_m8_conformance.py       # NEW — 4 end-to-end tests via mcp SDK
```

That's it — one new test file. No `src/` changes, no migrations, no
env-var additions, no dep updates. `mcp` was already a direct
dependency and already exposes `ClientSession` + `stdio_client` in
the versions we pin.

## Acceptance Checklist

- [x] `tests/test_m8_conformance.py` runs `ttio-mcp` as a real
      subprocess via `stdio_client` / `ClientSession`.
- [x] `test_conformance_initialize_and_list_tools` — asserts exact
      13-name tool set and schema-object shape for every tool.
- [x] `test_conformance_local_tools_round_trip` — exercises 12 of 13
      tools in a stateful sequence on one subprocess.
- [x] `test_conformance_push_file` — exercises the 13th tool
      (`ttio_push_file`) against the moto S3 fixture. Skips cleanly
      when cloud extras are missing (only one `importorskip` on
      `s3fs`; the session-scoped `moto_s3_server` fixture already
      imports the rest).
- [x] `test_conformance_error_envelope` — asserts the serialised
      `{ok: false, error: {code, message}}` shape for `not_found`.
- [x] `ruff check .` clean.
- [x] Full test suite green: **88 passed** (84 from M1–M7 + 4 new
      M8). On a machine without cloud extras the push-file test
      skips and the rest pass — suite stays green.
- [x] CHANGELOG entry under `[0.8.0.dev0]`. Version bump in
      `pyproject.toml` and `src/ttio_mcp/__init__.py`.
- [x] README milestone table splits the original "M8: Conformance +
      publish" row into M8 (shipped) and M9 (planned, TestPyPI).

## Workflow

Same as M1–M7: direct commits to `main`, `[M8] ...` prefix, push via
Windows git against `//wsl.localhost/...`.
