# HANDOFF-M4.md — TTI-O-MCP M4: Cloud I/O

## Context

M2 shipped the catalog with local-only URIs. M3 added query tools
(including the one tool — `ttio_get_spectrum` — that reopens the
underlying `.mpgo`). M4 makes the whole surface cloud-capable:
register and read `.mpgo` files directly from S3, GCS, Azure, or
plain HTTPS, without copying them to disk.

The win came for free from TTI-O v1.0.0: `SpectralDataset.open`
already accepts fsspec URIs (`s3://`, `gs://`, `az://`, `http(s)://`,
`gcs://`, `abfs(s)://`). TTI-O streams HDF5 metadata and reads
touched chunks lazily. M4 is almost entirely plumbing — accept the
URI at our resolver, hash it without crashing, and pass fsspec
kwargs through to TTI-O.

- M1 HANDOFF: [HANDOFF.md](HANDOFF.md) — binding decisions.
- M2 HANDOFF: [HANDOFF-M2.md](HANDOFF-M2.md) — catalog surface.
- M3 HANDOFF: [HANDOFF-M3.md](HANDOFF-M3.md) — query tools + the
  spectrum read path M4 extends.

## M4 Scope

- **Resolver accepts remote URIs.** `mpeg_o.remote.REMOTE_SCHEMES`
  (`s3`, `https`, `http`, `gs`, `gcs`, `az`, `abfs`, `abfss`, `file`)
  go through without path-resolution. Bare paths and `file://` keep
  the M2 behaviour.
- **Remote-aware hashing.** For cloud URIs, stream the bytes through
  `mpeg_o.remote.open_remote_file` into `hashlib.sha256`. Same 1 MiB
  chunk size as the local path. Caveat in the tool description: for
  large remote files this triggers a full read.
- **fsspec kwargs threading.** `Config` reads
  `TTIO_MCP_FSSPEC_KWARGS` (JSON object) and uses it as the default
  for every cloud call. `ttio_register_file` and `ttio_get_spectrum`
  accept an optional per-call `fsspec_kwargs` that overrides the
  default. Other tools don't need it (they answer from the catalog).
- **No schema changes.** `files.uri` already holds arbitrary strings;
  `file_sha256` is still a hex string. Alembic baseline unchanged
  through M4.
- **Documentation on reverify.** `ttio_reverify` for cloud files
  re-downloads the full object — note in the tool description,
  nothing schema-level.
- **Tests** against a `ThreadedMotoServer` fixture (moto[server,s3]).
  Skip cleanly when the cloud extras aren't installed.

## Out of Scope for M4

- **Keyring for credentials.** fsspec's default chain (env vars,
  `~/.aws/credentials`, IAM role, `~/.config/gcloud`, etc.) is
  enough for M4. Dedicated keyring integration lands in M5 alongside
  the auth / as_user work.
- **Encryption + signed bundles.** M5. The TTI-O
  `encrypt_with_key` / `decrypt_with_key` API is stable but integrating
  key management requires the same secrets plumbing as keyring, so
  they ship together.
- **Multi-user auth (`as_user`).** Still a no-op pending M5.
- **Caching.** fsspec has a `simplecache://` wrapper; we don't enable
  it in M4 — each `ttio_get_spectrum` call re-opens. If the clients
  hit it hard we can flip the config later.
- **Cross-region / multi-cloud creds hygiene.** Whatever credentials
  are in the default chain are what we use. No per-URI credential
  scoping until M5.

## Package Layout (new/changed in M4)

```
src/ttio_mcp/
├── config.py                  # UPDATED — fsspec_kwargs default
├── hashes.py                  # UPDATED — stream remote files too
├── catalog.py                 # UPDATED — resolve_uri + fsspec_kwargs
└── tools/
    ├── register.py            # UPDATED — accept fsspec_kwargs
    └── get_spectrum.py        # UPDATED — accept fsspec_kwargs

tests/
├── _cloud.py                  # NEW — ThreadedMotoServer fixture helper
└── test_cloud.py              # NEW — s3:// round-trip + spectrum read
```

## Config

```
TTIO_MCP_DB_URL          (unchanged)
TTIO_MCP_FSSPEC_KWARGS   JSON object, e.g.
                         {"client_kwargs": {"endpoint_url": "http://localhost:4566"}}
                         Merged with per-call fsspec_kwargs; per-call wins on key clash.
```

## Tool Contract Deltas

### `ttio_register_file` — add `fsspec_kwargs`

```json
{
  "uri":           {"type": "string"},
  "display_name":  {"type": "string"},
  "as_user":       {"type": "string"},
  "fsspec_kwargs": {
    "type": "object",
    "description": "Forwarded to fsspec.open for remote URIs. Typical keys: anon, key, secret, client_kwargs.endpoint_url, profile."
  }
}
```

### `ttio_get_spectrum` — add `fsspec_kwargs`

Same key, same semantics. No other tool accepts it — the rest of the
surface answers from the catalog.

### Error codes (unchanged set)

- `invalid_uri`, `resolve_failed`, `not_mpgo`, `read_failed`,
  `not_found`, `invalid_argument`, `internal`.
- **No new codes** in M4. A cloud fetch that 404s surfaces as
  `resolve_failed`; TTI-O refusing to parse surfaces as `not_mpgo`;
  an HTTP 5xx during spectrum read surfaces as `read_failed`.

## Acceptance Checklist

- [ ] Registering a local file still works (no regression).
- [ ] Registering an `s3://` URI under ThreadedMotoServer lands a
      row, hashes the bytes, and extracts the same catalog shape as
      the local equivalent.
- [ ] `ttio_get_spectrum` reads a single spectrum from the S3-hosted
      file and returns the same channels the local fixture does.
- [ ] Passing `fsspec_kwargs` per-call overrides `TTIO_MCP_FSSPEC_KWARGS`.
- [ ] `pytest -q` green without moto installed (cloud tests skip).
- [ ] `pytest -q` green with moto installed (cloud tests run).
- [ ] `ruff check .` clean.
- [ ] Alembic round-trip unchanged (no new migration).
- [ ] CHANGELOG entry under `[0.4.0.dev0]`. Version bump in
      `pyproject.toml` + `src/ttio_mcp/__init__.py`.
- [ ] Cloud extras documented in `pyproject.toml`:
      `cloud = ["fsspec", "s3fs"]`,
      `dev-cloud = ["moto[server,s3]"]` (separate so CI can pick
      either matrix).

## Workflow

Same as M1–M3: direct commits to `main`, `[M4] ...` prefix, push via
Windows git against `//wsl.localhost/...`.
