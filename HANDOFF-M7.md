# HANDOFF-M7.md — TTI-O-MCP M7: HMAC-SHA256 Dataset Signatures

## Context

M5 added in-place AES-256-GCM encryption plus a server-side JSON
keyring indexed by `key_id`. M6 added cloud push with optional
encrypt-on-upload. Neither touched the `files.signed` column — it
was provisioned in the baseline schema and never written.

M7 turns signing on. The new `ttio_sign_file` and
`ttio_verify_signature` tools wrap TTI-O v1.1.1's
`mpeg_o.signatures.sign_dataset` /
`mpeg_o.signatures.verify_dataset` APIs, which emit the canonical v2
HMAC-SHA256 tag into each signed dataset's `@ttio_signature` HDF5
attribute. No TTI-O-side changes were needed — v1.1.1 already
exposes the hash API with the `hmac-sha256` algorithm wired through.

- M1 HANDOFF: [HANDOFF.md](HANDOFF.md) — binding decisions.
- M2 HANDOFF: [HANDOFF-M2.md](HANDOFF-M2.md) — catalog surface.
- M3 HANDOFF: [HANDOFF-M3.md](HANDOFF-M3.md) — query tools.
- M4 HANDOFF: [HANDOFF-M4.md](HANDOFF-M4.md) — cloud I/O.
- M5 HANDOFF: [HANDOFF-M5.md](HANDOFF-M5.md) — keyring & encryption.
- M6 HANDOFF: [HANDOFF-M6.md](HANDOFF-M6.md) — cloud push.

## M7 Scope

- **`ttio_sign_file` tool.** Resolves a catalog entry to a local
  path, loads the HMAC-SHA256 key from the keyring via
  `keyring.get(key_id, expected_algorithm="hmac-sha256")`, opens the
  `.mpgo` with `h5py.File(path, "r+")`, walks every
  `signal_channels/*_values` dataset under both MS runs
  (`study/*/ms_runs/<run>/...`) and NMR runs
  (`study/*/nmr_runs/<run>/...`), and calls
  `signatures.sign_dataset(ds, key, algorithm="hmac-sha256")` on each.
  Re-signing overwrites any existing `@ttio_signature` attribute —
  there's one signature per dataset at any time. After signing, the
  on-disk bytes change (new VL attrs); the catalog row is refreshed:
  `signed=True`, `signature_algorithm="hmac-sha256"`,
  `signed_at=<now UTC>`, `signed_by=<users.id>`, plus new
  `file_sha256` / `content_sha256` / `last_verified_at`.
- **`ttio_verify_signature` tool.** Opens the file read-only, walks
  every dataset carrying an `@ttio_signature` attribute, and calls
  `signatures.verify_dataset` on each. Returns a
  `{hdf5_path: bool}` verdict map plus an aggregate `valid` flag that
  is true iff **every** signed dataset verified under the supplied
  key. Unsigned files (no `@ttio_signature` attrs anywhere) raise
  `not_signed` — we do not return `valid: true` for a file with
  nothing to check.
- **Algorithm-aware keyring.** `Keyring.get(key_id,
  expected_algorithm=...)` enforces that the stored `algorithm`
  field matches the caller's intent. AES-256-GCM still requires a
  32-byte key; `hmac-sha256` requires any non-empty byte string. A
  new `AlgorithmMismatch` exception (code `algorithm_mismatch`)
  surfaces cross-algorithm misuse.
- **AES-pinning everywhere else.** `ttio_encrypt_file` /
  `ttio_decrypt_file` / `ttio_push_file` / `ttio_get_spectrum` now
  all call `keyring.get(key_id, expected_algorithm=AES_256_GCM)`, so
  signing keys cannot be accidentally used for encryption and
  vice-versa.
- **Signature schema columns.** Alembic migration
  `3840d96e5185_signature_columns` (down_revision `65fda2fc1cfe`)
  adds three nullable columns to `files`: `signature_algorithm`,
  `signed_at`, `signed_by` (FK → `users.id`). The pre-existing
  boolean `signed` column is now populated.
- **Cloud and encrypted rejection.** Both tools reject cloud URIs
  (`remote_not_supported`) and encrypted files (`already_encrypted`)
  up front. Signing operates on plaintext byte layout, so encrypted
  files have nothing meaningful to sign. The documented workflow for
  cloud files stays manual: pull down, sign locally, re-push with
  `ttio_push_file`. Covered in DEPLOYMENT-GUIDE.md §"Signing `.mpgo`
  files".

## Out of Scope for M7

- **Public-key signatures (ML-DSA-87 v3).** The TTI-O standard
  defines both HMAC-SHA256 (v2) and ML-DSA-87 (v3) tags. M7 wraps
  only v2. ML-DSA-87 requires an asymmetric keystore (private key
  server-side, public key distributable) and a different error
  surface; tracked as a separate future milestone.
- **SpectralDataset-level sign/verify API.** We sign at the h5py
  dataset level because TTI-O v1.1.1 ships
  `signatures.sign_dataset` but no `SpectralDataset.sign_with_key`
  convenience. Adding such a method is an TTI-O-side task; until
  then, the MCP tool does the walk itself. The set of datasets we
  sign is exactly what TTI-O's own test suite checks, so the wire
  format is identical.
- **Atomic rotate.** `ttio_sign_file` is idempotent per dataset —
  re-signing with a new key overwrites the attribute — but there is
  no transactional "rotate to key B iff verify under key A" tool.
  Two calls, same as the decrypt/encrypt rekey workflow.
- **Sign-then-encrypt / encrypt-then-sign orchestration.** Neither
  direction is plumbed as a single tool. Encryption changes dataset
  byte layout in ways that invalidate v2 canonical-form HMACs; if
  callers want both, the documented order is sign-first, encrypt-
  later, and only verify the decrypted plaintext.
- **KMS-backed keyring.** Still a flat JSON file. The algorithm-
  aware `get()` is ready to host a KMS backend (each `key_id` can
  carry any algorithm tag), but no backend is added in M7.
- **Verify against cloud URIs.** The verify path requires byte-stable
  h5py reads. Supporting remote URIs would mean either streaming the
  file through a temp cache or extending TTI-O's remote helper to
  back an h5py `File`. Out of scope — the manual workflow is
  download-and-register locally first.

## Package Layout (new/changed in M7)

```
src/ttio_mcp/
├── keyring.py                    # UPDATED — HMAC_SHA256 const,
│                                 #   SUPPORTED_ALGORITHMS frozenset,
│                                 #   AlgorithmMismatch, algorithm-
│                                 #   scoped get(), variable-length
│                                 #   _validate_key_bytes
├── db/
│   └── models.py                 # UPDATED — files gains
│                                 #   signature_algorithm, signed_at,
│                                 #   signed_by (FK users.id)
└── tools/
    ├── __init__.py               # UPDATED — 13 tools (adds
    │                             #   ttio_sign_file,
    │                             #   ttio_verify_signature)
    ├── _helpers.py               # UPDATED — file_to_dict exposes
    │                             #   signature_algorithm / signed_at
    │                             #   / signed_by
    ├── encrypt_file.py           # UPDATED — key pinned to AES-256-GCM
    ├── decrypt_file.py           # UPDATED — key pinned to AES-256-GCM
    ├── get_spectrum.py           # UPDATED — key pinned to AES-256-GCM
    ├── push_file.py              # UPDATED — key pinned to AES-256-GCM
    ├── sign_file.py              # NEW     — ttio_sign_file handler
    └── verify_signature.py       # NEW     — ttio_verify_signature handler

migrations/versions/
└── 3840d96e5185_signature_columns.py   # NEW

tests/
├── test_m3_tools.py              # UPDATED — surface count 11 → 13
├── test_m5_keyring.py            # UPDATED — +3 HMAC / algorithm tests
└── test_m7_sign_verify.py        # NEW     — 7 sign / verify tests
```

No new env vars. The same `TTIO_KEYRING_PATH` holds both AES-256-GCM
and HMAC-SHA256 entries.

## Tool Contract

### `ttio_sign_file` — new

```json
{
  "id":      {"type": "integer", "minimum": 1},
  "uri":     {"type": "string"},
  "key_id":  {"type": "string"},
  "as_user": {"type": "string"}
}
```

Required: `key_id`. Plus `oneOf({id, key_id}, {uri, key_id})`.

Returns:

```json
{
  "file_id": 1,
  "uri": "file:///data/sample.mpgo",
  "signed": true,
  "signature_algorithm": "hmac-sha256",
  "signed_at": "2026-04-24T12:00:00+00:00",
  "signed_by": 1,
  "key_id": "release-signer",
  "signed_datasets": [
    "/study/demo/ms_runs/ms1/signal_channels/intensity_values",
    "/study/demo/ms_runs/ms1/signal_channels/mz_values"
  ],
  "signed_dataset_count": 2,
  "file_sha256": "<64-char hex>",
  "content_sha256": "<64-char hex>"
}
```

### `ttio_verify_signature` — new

```json
{
  "id":     {"type": "integer", "minimum": 1},
  "uri":    {"type": "string"},
  "key_id": {"type": "string"}
}
```

Required: `key_id`. Plus `oneOf({id, key_id}, {uri, key_id})`. No
`as_user` — verification is read-only and doesn't touch the catalog.

Returns:

```json
{
  "file_id": 1,
  "uri": "file:///data/sample.mpgo",
  "valid": true,
  "signature_algorithm": "hmac-sha256",
  "signed_at": "2026-04-24T12:00:00+00:00",
  "signed_by": 1,
  "key_id": "release-signer",
  "verified_datasets": {
    "/study/demo/ms_runs/ms1/signal_channels/intensity_values": true,
    "/study/demo/ms_runs/ms1/signal_channels/mz_values": true
  },
  "verified_dataset_count": 2
}
```

A wrong key (or tampered bytes) flips per-dataset verdicts to `false`
and the aggregate `valid` to `false`, but the tool still returns
`ok: true` — verification ran successfully, it just didn't pass.
`verify_failed` is reserved for I/O / HDF5 errors.

### Error codes (additions)

- `algorithm_mismatch` — key's stored `algorithm` doesn't match the
  tool's requirement (e.g. HMAC key passed to `ttio_encrypt_file`,
  or AES key passed to `ttio_sign_file`).
- `sign_failed` — h5py / TTI-O raised while walking or signing.
- `verify_failed` — h5py / TTI-O raised while walking or verifying.
- `nothing_to_sign` — no `signal_channels/*_values` datasets found
  in the file (either not an `.mpgo`, or an empty one).
- `not_signed` — verification target has no datasets with an
  `@ttio_signature` attribute. Kept distinct from `valid: false` so
  callers can't mistake "nothing to check" for success.

Existing codes that can still surface: `not_found` (catalog lookup
miss), `already_encrypted` (file is encrypted; decrypt first),
`remote_not_supported` (cloud URI), `keyring_not_configured`,
`key_not_found`, `invalid_keyring`, `unknown_user` (sign only; verify
doesn't resolve a user).

## Acceptance Checklist

- [x] `ttio_sign_file` on a plaintext fixture stamps every
      `signal_channels/*_values` dataset with `@ttio_signature`,
      refreshes `file_sha256` / `content_sha256`, and sets
      `files.signed=True`, `files.signature_algorithm="hmac-sha256"`,
      `files.signed_at`, `files.signed_by`.
- [x] `ttio_verify_signature` under the same `key_id` returns
      `valid: true` and a per-dataset verdict map with every verdict
      `true`.
- [x] Wrong `key_id` produces `valid: false` with per-dataset
      verdicts all `false` — no raise.
- [x] Verifying an unsigned file raises `not_signed` (not
      `valid: false`).
- [x] Passing an `AES-256-GCM` `key_id` to `ttio_sign_file` raises
      `algorithm_mismatch`.
- [x] `ttio_sign_file` and `ttio_verify_signature` against an
      encrypted file raise `already_encrypted`.
- [x] `ttio_sign_file` and `ttio_verify_signature` against an
      `s3://` URI raise `remote_not_supported`.
- [x] Alembic round-trip clean: `upgrade head` adds the three new
      columns, `downgrade -1` drops them, `upgrade head` restores.
- [x] `ruff check .` clean.
- [x] Full test suite green: **84 passed** (74 from M1–M6 + 10 new:
      7 in `test_m7_sign_verify.py` + 3 in `test_m5_keyring.py`).
- [x] CHANGELOG entry under `[0.7.0.dev0]`. Version bump in
      `pyproject.toml` and `src/ttio_mcp/__init__.py`.

## Workflow

Same as M1–M6: direct commits to `main`, `[M7] ...` prefix, push via
Windows git against `//wsl.localhost/...`.
