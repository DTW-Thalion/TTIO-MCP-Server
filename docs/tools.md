# Tool reference

The server registers 11 MCP tools. All responses use the envelope:

```jsonc
// success
{"ok": true,  "data": { /* tool-specific */ }}

// error
{"ok": false, "error": {"code": "<code>", "message": "<human readable>"}}
```

Every input schema has `additionalProperties: false` — unknown fields
are rejected at the MCP layer before the handler runs.

## Conventions

- **URIs.** Registration accepts `file://` URIs, bare absolute paths,
  and cloud URIs (`s3://`, `https://`, `http://`, `gs://`, `gcs://`,
  `abfs://`, `abfss://`, `az://`) — anything
  `mpeg_o.remote.is_remote_url` recognises. Encrypt and decrypt are
  local-only and reject cloud URIs.
- **`as_user`.** Every tool that mutates the catalog accepts an
  optional `as_user` string. The name must already exist in the
  `users` table — unknown names raise `unknown_user`. Absent
  `as_user` defaults to the seeded `system` user.
- **`fsspec_kwargs`.** On `mpgo_register_file` and `mpgo_get_spectrum`,
  a per-call `fsspec_kwargs` object is shallow-merged on top of
  `MPGO_MCP_FSSPEC_KWARGS` (per-call keys win). Ignored for local
  files.
- **Keyring.** Encrypt, decrypt, and encrypted-spectrum reads resolve
  `key_id` through the server-side keyring (`MPGO_KEYRING_PATH`). Raw
  key bytes never cross the MCP wire.
- **Pagination.** List tools use `limit` (default 50, max 500) and
  `offset` (default 0) and return `{total, limit, offset, <items>}`.

---

## Catalog & registration

### `mpgo_register_file`

Register an `.mpgo` in the catalog. Resolves the URI, hashes the bytes,
opens the file through `mpeg_o.SpectralDataset`, harvests metadata,
and upserts rows atomically. Idempotent on `uri` — re-registering the
same URI updates the `files` row and replaces child rows
(`studies`, `runs`, `identifications`, `quantifications`,
`provenance_records`).

**Input**

| Field | Type | Notes |
|---|---|---|
| `uri` *(required)* | string | `file://`, bare path, or cloud URI. |
| `display_name` | string | Optional friendly name. |
| `as_user` | string | Must exist in `users`. Defaults to `system`. |
| `fsspec_kwargs` | object | Merged onto `MPGO_MCP_FSSPEC_KWARGS` for remote URIs. |

**Success data**

```json
{
  "file_id": 1,
  "uri": "file:///data/sample.mpgo",
  "file_sha256": "...",
  "format_version": "1.0",
  "features": {"encryption": false, "signing": false, "...": "..."},
  "counts": {"studies": 1, "runs": 3, "identifications": 42, "quantifications": 0, "provenance_records": 2},
  "was_update": false
}
```

**Errors:** `invalid_uri`, `resolve_failed`, `not_mpgo`, `unknown_user`.

### `mpgo_list_files`

Paginated catalog listing with optional filters. Never touches disk.

**Input**

| Field | Type | Notes |
|---|---|---|
| `limit` | integer 1-500 | Default 50. |
| `offset` | integer ≥0 | Default 0. |
| `title_contains` | string | ILIKE match against `studies.title`. |
| `acquisition_mode` | string | Exact match against any run (e.g. `MS1_DDA`, `NMR_1D`). |

**Success data**

```json
{
  "total": 12,
  "limit": 50,
  "offset": 0,
  "files": [ { /* file_to_dict */ } ]
}
```

### `mpgo_get_file`

Full record for one file, including studies and runs. Never touches disk.

**Input** — `{id}` *or* `{uri}` (oneOf).

**Success data** — the `file_to_dict` shape plus `counts`, `studies[]`,
and `runs[]` summaries.

**Errors:** `not_found`.

### `mpgo_reverify`

Re-hash the referenced bytes, update `last_verified_at`, and report
drift. **Local files only** — remote URIs raise `invalid_uri`. If the
bytes can't be read (local file missing, etc.) the handler returns a
non-raising result with `resolved: false` and an `error` string.

**Input** — `{id}` *or* `{uri}` (oneOf).

**Success data**

```json
{
  "file_id": 1,
  "uri": "file:///data/sample.mpgo",
  "resolved": true,
  "drift": false,
  "file_sha256": "...",
  "last_verified_at": "2026-04-24T12:00:00+00:00"
}
```

**Non-raising failure (local file missing, etc.)**

```json
{
  "file_id": 1,
  "uri": "file:///data/sample.mpgo",
  "resolved": false,
  "error": "/data/sample.mpgo does not exist"
}
```

**Errors:** `not_found`, `invalid_uri` (remote URI).

---

## Query

### `mpgo_search_identifications`

Cross-file identification search. Never touches disk. Ranked by
`score DESC, id ASC`.

**Input**

| Field | Type | Notes |
|---|---|---|
| `chebi_id` | string | Exact. |
| `name_contains` | string | `LIKE %…%` on `identifications.name`. |
| `min_score` | number 0-1 | |
| `acquisition_mode` | string | Joined via `runs`. |
| `file_id` | integer ≥1 | |
| `limit` / `offset` | standard pagination | |

**Success data** — `{total, limit, offset, identifications: [{id, file_id, file_uri, run_id, run_name, acquisition_mode, chebi_id, name, score, spectrum_index, evidence_chain}]}`.

### `mpgo_get_run`

Per-run detail with inline identifications and quantifications scoped
to this run (`sample_ref == run.name` or NULL).

**Input** — `{run_id}` *or* `{file_id, run_name}` (oneOf).

**Success data** — run metadata plus `identifications[]` and
`quantifications[]`. Includes `nucleus_type` and `channel_names`
when the run metadata carries them.

**Errors:** `not_found`.

### `mpgo_get_spectrum`

The only query tool that reopens the underlying `.mpgo`. Channels
past `max_points` are stride-downsampled and flagged with
`truncated: true`.

**Input**

| Field | Type | Notes |
|---|---|---|
| `run_id` | integer ≥1 | Alternative 1. |
| `file_id` + `run_name` | integer + string | Alternative 2. |
| `spectrum_index` *(required)* | integer ≥0 | |
| `max_points` | integer 1-100000 | Default 1000. |
| `fsspec_kwargs` | object | Remote URIs only. |
| `key_id` | string | Required when `files.encrypted=true`. |

**Success data**

```json
{
  "run_id": 7,
  "run_name": "ms1",
  "spectrum_index": 42,
  "spectrum_class": "MassSpectrum",
  "channels": {"mz": [...], "intensity": [...]},
  "metadata": {"ms_level": 1, "retention_time": 123.4, ...},
  "truncated": false,
  "original_length": 850,
  "returned_length": 850
}
```

**Errors:** `not_found`, `invalid_argument`, `read_failed`,
`key_required` (encrypted file, no key), `key_not_found`,
`keyring_not_configured`, `invalid_keyring`.

### `mpgo_get_quantifications`

Per-file quantification listing with filters. Never touches disk.

**Input** — `{file_id}` *or* `{uri}` (oneOf) plus optional
`chebi_id`, `sample_ref`, `min_abundance`, `limit`, `offset`.

**Success data** — `{file_id, total, limit, offset,
quantifications: [{id, chebi_id, name, sample_ref, abundance,
normalization_method}]}`.

**Errors:** `not_found`.

---

## Encryption (M5)

Both tools are **local-only** — cloud URIs are rejected with
`remote_not_supported`. Both require a configured keyring via
`MPGO_KEYRING_PATH`; see [configuration.md](configuration.md#keyring).

### `mpgo_encrypt_file`

In-place AES-256-GCM intensity encryption via
`mpeg_o.SpectralDataset.encrypt_with_key`. Rehashes the file, flips
`files.encrypted`, stamps `files.encrypted_algorithm`, refreshes
`last_verified_at`.

**Input**

| Field | Type | Notes |
|---|---|---|
| `id` | integer ≥1 | Alternative 1 (with `key_id`). |
| `uri` | string | Alternative 2 (with `key_id`). |
| `key_id` *(required)* | string | Keyring id. |
| `level` | enum | `DATASET_GROUP` (default), `DATASET`, `DESCRIPTOR_STREAM`, `ACCESS_UNIT`. |
| `as_user` | string | |

**Success data**

```json
{
  "file_id": 1,
  "uri": "file:///data/sample.mpgo",
  "encrypted": true,
  "encrypted_algorithm": "AES-256-GCM",
  "level": "DATASET_GROUP",
  "key_id": "demo",
  "file_sha256": "...",
  "content_sha256": "..."
}
```

**Errors:** `already_encrypted`, `remote_not_supported`,
`encrypt_failed`, `keyring_not_configured`, `key_not_found`,
`invalid_keyring`, `unknown_user`.

### `mpgo_decrypt_file`

Persist plaintext back to disk via MPEG-O v1.1.1
`SpectralDataset.decrypt_in_place`. Mirrors the encrypt bookkeeping:
clears `encrypted_algorithm`, rehashes.

**Input** — `{id, key_id}` *or* `{uri, key_id}`. Plus optional `as_user`.

**Success data**

```json
{
  "file_id": 1,
  "uri": "file:///data/sample.mpgo",
  "encrypted": false,
  "encrypted_algorithm": null,
  "key_id": "demo",
  "file_sha256": "...",
  "content_sha256": "..."
}
```

**Errors:** `not_encrypted`, `remote_not_supported`, `decrypt_failed`,
`keyring_not_configured`, `key_not_found`, `invalid_keyring`,
`unknown_user`.

> **Re-keying.** There is no atomic rotate operation. Decrypt with
> the old `key_id`, then encrypt with the new one in two calls.

---

## Cloud push (M6)

### `mpgo_push_file`

Upload a local `.mpgo` to a writable cloud URI and register the uploaded
object in the catalog in one call. Optionally encrypts the bytes with
AES-256-GCM in-flight so plaintext never touches the bucket.

**What it does NOT do:** post-hoc encrypt an object that already lives
in the cloud. For that flow, pull down with your cloud client, run
`mpgo_encrypt_file` locally, then push the ciphertext with this tool.

**Input**

| Field | Type | Notes |
|---|---|---|
| `local_uri` *(required)* | string | Local source: `file://` URI or absolute path. Never modified. |
| `remote_uri` *(required)* | string | Destination. Must be one of `s3://`, `gs://`, `gcs://`, `abfs://`, `abfss://`, `az://`. |
| `key_id` | string | When set, a throwaway temp copy is encrypted before upload. The ciphertext is what lands at `remote_uri`. |
| `level` | enum | `DATASET_GROUP` (default), `DATASET`, `DESCRIPTOR_STREAM`, `ACCESS_UNIT`. Only consulted when `key_id` is set. |
| `as_user` | string | Ownership for the new catalog row. |
| `fsspec_kwargs` | object | Shallow-merged on top of `MPGO_MCP_FSSPEC_KWARGS`. Forwarded to both the upload write and the post-upload register. |

**Success data**

```json
{
  "file_id": 7,
  "uri": "s3://bucket/path/sample.mpgo",
  "remote_uri": "s3://bucket/path/sample.mpgo",
  "file_sha256": "...",
  "encrypted": true,
  "encrypted_algorithm": "AES-256-GCM",
  "key_id": "prod-2026q2",
  "counts": {"studies": 1, "runs": 3, "identifications": 42, "quantifications": 0, "provenance_records": 2},
  "was_update": false
}
```

**Errors:** `scheme_not_writable`, `resolve_failed`, `upload_failed`,
`encrypt_failed`, `keyring_not_configured`, `key_not_found`,
`invalid_keyring`, `not_mpgo`, `unknown_user`.

The catalog row for `remote_uri` is created (or updated, when
re-pushing to the same key) through the normal `mpgo_register_file`
path, so all subsequent query tools (`mpgo_get_file`,
`mpgo_search_identifications`, `mpgo_get_spectrum`, ...) see the
uploaded object exactly as if it had been registered manually.

---

## Error codes

Stable strings emitted in `error.code`. Codes are grouped by origin.

### Catalog

| Code | Meaning |
|---|---|
| `invalid_uri` | URI didn't parse or scheme is unsupported. |
| `resolve_failed` | URI resolved but bytes couldn't be read/streamed. |
| `not_mpgo` | File opened but is not a valid `.mpgo`. |
| `not_found` | Catalog lookup (`id` / `uri` / `run_id` / `run_name`) missed. |
| `unknown_user` | `as_user` not present in the `users` table (M5). |
| `internal` | Uncategorised server-side error. |

### Tool-layer

| Code | Origin | Meaning |
|---|---|---|
| `invalid_argument` | `get_spectrum` | e.g. `spectrum_index` past `spectrum_count`. |
| `read_failed` | `get_spectrum` | Opening the file or reading the spectrum threw. |
| `key_required` | `get_spectrum` | Encrypted file, no `key_id` supplied. |
| `already_encrypted` | `encrypt_file` | Catalog marks file encrypted. |
| `not_encrypted` | `decrypt_file` | Catalog marks file plaintext. |
| `remote_not_supported` | `encrypt_file` / `decrypt_file` | Cloud URI rejected. |
| `encrypt_failed` | `encrypt_file` / `push_file` | MPEG-O-side exception during encrypt. |
| `decrypt_failed` | `decrypt_file` | MPEG-O-side exception during decrypt. |
| `scheme_not_writable` | `push_file` | `remote_uri` scheme is not a writable cloud scheme. |
| `upload_failed` | `push_file` | fsspec write to the remote URI raised. |

### Keyring

| Code | Meaning |
|---|---|
| `keyring_not_configured` | `MPGO_KEYRING_PATH` unset. |
| `key_not_found` | `key_id` absent from the keyring. |
| `invalid_keyring` | Malformed JSON, wrong algorithm, wrong length, bad base64. |

### Server

| Code | Meaning |
|---|---|
| `unknown_tool` | MCP client called a tool name not in the registry. |
| `internal` | Uncaught exception escaping the handler. |
