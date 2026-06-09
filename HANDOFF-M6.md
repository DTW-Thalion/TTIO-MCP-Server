# HANDOFF-M6.md — TTI-O-MCP M6: Cloud Push + Encrypt-on-Upload

## Context

M4 made the catalog cloud-capable on the read side (streaming
register + spectrum reads from `s3://`/`gs://`/`abfs://` via fsspec).
M5 added server-side encryption, but hard-rejected cloud URIs with
`remote_not_supported` — there was no supported way to publish a
local `.mpgo` to the cloud at all, encrypted or otherwise.

M6 fills the write side. The new `ttio_push_file` tool uploads a local
file to a writable cloud URI, optionally encrypting it on the way, and
registers the uploaded object in the catalog. The local source is
never modified.

- M1 HANDOFF: [HANDOFF.md](HANDOFF.md) — binding decisions.
- M2 HANDOFF: [HANDOFF-M2.md](HANDOFF-M2.md) — catalog surface.
- M3 HANDOFF: [HANDOFF-M3.md](HANDOFF-M3.md) — query tools.
- M4 HANDOFF: [HANDOFF-M4.md](HANDOFF-M4.md) — cloud I/O.
- M5 HANDOFF: [HANDOFF-M5.md](HANDOFF-M5.md) — keyring & encryption.

## M6 Scope

- **`ttio_push_file` tool.** Streams a local `.mpgo` to a writable
  cloud URI via `fsspec.open(dest, "wb", **kwargs)`, then calls
  `register_file(session, remote_uri, ...)` to create (or update) the
  catalog row. Writable schemes: `s3://`, `gs://`, `gcs://`,
  `abfs://`, `abfss://`, `az://`. `http://`/`https://`/`file://` are
  rejected up front with the new `scheme_not_writable` error code.
- **Optional encrypt-on-upload.** When `key_id` is supplied, the tool
  copies the source into a tempfile, encrypts the tempfile in place
  via the existing M5 code path
  (`SpectralDataset.open(path, writable=True).encrypt_with_key(key, level)`),
  uploads the ciphertext, and deletes the tempfile. Only ciphertext
  ever reaches the bucket. The `level` argument mirrors
  `ttio_encrypt_file`.
- **Post-upload catalog flag.** After a successful encrypted push, the
  new catalog row is updated with `encrypted=True`,
  `encrypted_algorithm="AES-256-GCM"`, and refreshed
  `last_verified_at`. Register itself doesn't know the file is
  encrypted — TTI-O metadata (title, runs, identifications,
  quantifications) is plaintext and extracts fine without the key.
- **Documented manual workflow.** For a file that *already* lives in
  the cloud, post-hoc encryption stays manual:
  (1) pull it down,
  (2) `ttio_encrypt_file` on the local copy,
  (3) `ttio_push_file` with no `key_id` (ciphertext already).
  DEPLOYMENT-GUIDE.md section "Publishing to the cloud" covers this.

## Out of Scope for M6

- **Server-side post-hoc encryption of cloud objects.** Object stores
  are immutable at the object level; any "encrypt in place" for a
  cloud URI pays at minimum one full upload, plus one full download
  if the server must fetch the plaintext first. Until there's a
  compelling use case that isn't served by the manual
  pull→encrypt→push workflow, this stays off the menu.
- **Streaming encryption.** The encrypt-on-push path fully stages the
  file on local disk before uploading. TTI-O's
  `encrypt_with_key` operates on an opened HDF5 file, not a stream —
  a true streaming path would require a new TTI-O API. Deferred.
- **Multipart uploads / resumable uploads.** fsspec handles what it
  handles; we don't add retry or multipart bookkeeping on top.
- **Signed bundles.** `files.signed` still unused; lands in M7 along
  with the pluggable KMS keyring.
- **KMS-backed keyring.** The keyring is still a flat JSON file. The
  `key_id` indirection is ready to host a KMS backend, but we do not
  add one in M6.

## Package Layout (new/changed in M6)

```
src/ttio_mcp/
└── tools/
    ├── __init__.py      # UPDATED — registers ttio_push_file (11 tools total)
    └── push_file.py     # NEW — ttio_push_file handler

tests/
├── test_m3_tools.py         # UPDATED — surface count 10 → 11
└── test_m6_push_file.py     # NEW — 6 tests against moto S3 fixture
```

No new migrations (the M5 `files.encrypted_algorithm` column covers
what encrypt-on-push needs). No new env vars.

## Tool Contract

### `ttio_push_file` — new

```json
{
  "local_uri":     {"type": "string"},
  "remote_uri":    {"type": "string"},
  "key_id":        {"type": "string"},
  "level":         {"type": "string", "enum": ["DATASET_GROUP", "DATASET", "DESCRIPTOR_STREAM", "ACCESS_UNIT"]},
  "as_user":       {"type": "string"},
  "fsspec_kwargs": {"type": "object", "additionalProperties": true}
}
```

Required: `local_uri`, `remote_uri`.

Returns:

```json
{
  "file_id": 7,
  "uri": "s3://bucket/path/sample.mpgo",
  "remote_uri": "s3://bucket/path/sample.mpgo",
  "file_sha256": "<64-char hex>",
  "encrypted": true,
  "encrypted_algorithm": "AES-256-GCM",
  "key_id": "prod-2026q2",
  "counts": {"studies": 1, "runs": 3, "identifications": 42, "quantifications": 0, "provenance_records": 2},
  "was_update": false
}
```

`encrypted` / `encrypted_algorithm` / `key_id` are `false` / `null` /
`null` when no `key_id` was supplied.

### Error codes (additions)

- `scheme_not_writable` — `remote_uri` scheme is not one of `s3`,
  `gs`, `gcs`, `abfs`, `abfss`, `az`. Also raised when `local_uri`
  points at a remote scheme (wrong tool for the job).
- `upload_failed` — fsspec raised while writing to `remote_uri`.

Existing codes that can still surface through `push_file`:
`resolve_failed` (local source missing), `encrypt_failed`,
`keyring_not_configured`, `key_not_found`, `invalid_keyring`,
`not_mpgo` (register couldn't open the uploaded bytes — should only
happen if the source wasn't a valid `.mpgo` to start with),
`unknown_user`.

## Acceptance Checklist

- [x] Plaintext push to `s3://` lands the exact local bytes (sha256
      matches the local file byte-for-byte) and registers the row
      with `encrypted=False`.
- [x] Encrypted push lands ciphertext (sha256 differs from local),
      leaves the local file untouched (pre-push hash preserved), and
      registers the row with `encrypted=True` /
      `encrypted_algorithm="AES-256-GCM"`. Reading a spectrum from
      the new `s3://` URI works when the same `key_id` is supplied.
- [x] `https://` and `file://` destinations rejected with
      `scheme_not_writable`.
- [x] Missing local source → `resolve_failed`.
- [x] Unknown `key_id` → `key_not_found` with no bucket write
      attempted.
- [x] `ruff check .` clean.
- [x] Full test suite green: **74 passed** (68 from M1–M5 + 6 new M6).
- [x] CHANGELOG entry under `[0.6.0.dev0]`. Version bump in
      `pyproject.toml` and `src/ttio_mcp/__init__.py`.

## Workflow

Same as M1–M5: direct commits to `main`, `[M6] ...` prefix, push via
Windows git against `//wsl.localhost/...`.
