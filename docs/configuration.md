# Configuration

All configuration is read from the environment of the process that
launches `mpeg-o-mcp`. The server never accepts secrets through MCP
tool arguments.

| Variable | Default | Purpose |
|---|---|---|
| `MPGO_MCP_DB_URL` | `sqlite:///mpeg_o_mcp.db` | SQLAlchemy URL for the catalog. |
| `MPGO_MCP_FSSPEC_KWARGS` | *(unset)* | JSON object merged into every `fsspec.open` call for cloud URIs. |
| `MPGO_KEYRING_PATH` | *(unset)* | Filesystem path to the JSON keyring used by the encryption tools. |

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
- `value` must decode as exactly 32 bytes of base64. Anything else
  raises `invalid_keyring`.
- `algorithm` defaults to `AES-256-GCM`; only that algorithm is
  currently supported. Other values raise `invalid_keyring`.
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

## Transport

stdio only in v0.1. Config knobs for the MCP transport itself live in
whatever launches the server — `claude mcp add mpeg-o-mcp -- mpeg-o-mcp`
and equivalents. The server name (`mpeg-o-mcp`) and version (from
`mpeg_o_mcp.__version__`) are reported in the `initialize` response.
