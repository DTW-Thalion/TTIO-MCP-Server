# Configuration

All configuration is read from the environment of the process that
launches `mpeg-o-mcp`. The server never accepts secrets through MCP
tool arguments.

| Variable | Default | Purpose |
|---|---|---|
| `MPGO_MCP_DB_URL` | `sqlite:///mpeg_o_mcp.db` | SQLAlchemy URL for the catalog. |
| `MPGO_MCP_FSSPEC_KWARGS` | *(unset)* | JSON object merged into every `fsspec.open` call for cloud URIs. |
| `MPGO_KEYRING_PATH` | *(unset)* | Filesystem path to the JSON keyring used by the encryption tools. |
| `MPGO_MCP_INTAKE_DIR` | *(unset)* | Directory where `mpgo_launch_uploader` stages files chosen by the user. |

Cloud credentials (AWS, GCP, Azure) are picked up by `fsspec` / `s3fs`
from their usual sources — env vars, profile files, IMDS, workload
identity. The server does not read them itself.

## `MPGO_MCP_DB_URL`

Any SQLAlchemy URL works. Common shapes:

```bash
# Local SQLite (default)
export MPGO_MCP_DB_URL="sqlite:///mpeg_o_mcp.db"

# Postgres
export MPGO_MCP_DB_URL="postgresql+psycopg://user:pw@host:5432/mpeg_o_mcp"
```

After setting, bootstrap the schema:

```bash
alembic upgrade head
```

The initial migration seeds a `system` user (id=1). All subsequent
catalog writes default their `registered_by` / `owner_user_id` to
this row unless a tool call supplies `as_user` for a pre-provisioned
name. `alembic downgrade base` reverses every migration.

## `MPGO_MCP_FSSPEC_KWARGS`

A JSON object forwarded to `fsspec.open` whenever a cloud URI is
resolved. Per-call `fsspec_kwargs` on `mpgo_register_file` and
`mpgo_get_spectrum` **shallow-merge on top** (per-call keys win).

```bash
# Private S3 bucket — use the caller's default AWS credentials
export MPGO_MCP_FSSPEC_KWARGS='{"anon": false}'

# Public bucket
export MPGO_MCP_FSSPEC_KWARGS='{"anon": true}'

# Custom S3 endpoint (MinIO, LocalStack, etc.)
export MPGO_MCP_FSSPEC_KWARGS='{
  "anon": false,
  "client_kwargs": {"endpoint_url": "https://minio.example:9000"}
}'
```

Invalid JSON or a non-object value aborts server startup with a clear
error. An unset variable is equivalent to `{}`.

Supported cloud schemes (anything
`mpeg_o.remote.is_remote_url` recognises): `s3://`, `https://`,
`http://`, `gs://`, `gcs://`, `abfs://`, `abfss://`, `az://`. Install
the `cloud` extra (`pip install -e ".[cloud]"`) to pull in `s3fs`
and `fsspec`.

## `MPGO_KEYRING_PATH`

Path to the JSON keyring used by `mpgo_encrypt_file`,
`mpgo_decrypt_file`, and encrypted reads via `mpgo_get_spectrum`.

Missing file = empty keyring; the error only surfaces when a tool
looks up a specific `key_id`. No keyring at all (env unset) means
encrypt / decrypt / encrypted-reads fail with
`keyring_not_configured`.

### File format

```json
{
  "keys": {
    "demo": {
      "value": "base64-encoded 32 bytes",
      "algorithm": "AES-256-GCM",
      "created_at": "2026-04-24T12:00:00+00:00",
      "description": "optional free text"
    },
    "prod-2026q2": {
      "value": "...",
      "algorithm": "AES-256-GCM"
    }
  }
}
```

Rules:

- Top level must be an object with a `keys` object.
- Each entry must be an object with a string `value`.
- `value` must decode as valid base64. Length rules are
  per-algorithm (see below).
- `algorithm` defaults to `AES-256-GCM`. Two algorithms are supported:
  - `AES-256-GCM` — bulk encryption; key must decode to exactly 32 bytes.
    Used by `mpgo_encrypt_file`, `mpgo_decrypt_file`, `mpgo_push_file`,
    and encrypted-`get_spectrum` reads.
  - `hmac-sha256` — dataset signing; key must decode to at least 1 byte
    (32 bytes is conventional). Used by `mpgo_sign_file` and
    `mpgo_verify_signature`.
  Any other value raises `invalid_keyring`. Each tool pins the
  algorithm it expects, so a key tagged for one algorithm cannot be
  used with the other (`algorithm_mismatch`).
- `created_at` and `description` are metadata only — they surface
  through the keyring's listing API but never through tool responses.

### Generating a key

```bash
python -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())'
```

Drop the output into `value`. Never commit the keyring file.

### Keys never cross the MCP wire

Tool callers pass a `key_id`; the server resolves it to raw bytes
in-process via `mpeg_o_mcp.keyring.Keyring.get`. Responses carry only
the `key_id` string.

## `MPGO_MCP_INTAKE_DIR`

Destination directory for files staged through `mpgo_launch_uploader`.
Required before the tool can run — absent configuration surfaces as
the `intake_not_configured` error.

```bash
export MPGO_MCP_INTAKE_DIR="$HOME/mpeg-o/intake"
```

When a file is chosen, the uploader copies it into this directory
using the source filename. If a file with that name already exists, a
UTC timestamp is inserted before the extension
(`sample.mpgo` → `sample-20260424T120000Z.mpgo`); repeat collisions
add an integer counter. The original on disk is never modified.

The directory is auto-created on first write (server-side), so it's
safe to point at a path that doesn't yet exist. Only the server
process needs access — the tkinter picker runs in a subprocess that
inherits the server's environment.

Since the uploader opens a tkinter window, the server must run on a
host with a display (the same machine as the MCP client, which is the
normal stdio deployment). Headless deployments (containers, SSH
without X11) will get `no_display` back — keep the catalog import
tools for those workflows.

## Transport

stdio only. Config knobs for the MCP transport itself live in
whatever launches the server — `claude mcp add mpeg-o-mcp -- mpeg-o-mcp`
and equivalents. The server name (`mpeg-o-mcp`) and version (from
`mpeg_o_mcp.__version__`) are reported in the `initialize` response.
MCP-over-HTTP and SSE are not implemented; run the server over SSH
or an external stdio↔HTTP proxy if you need remote access.
