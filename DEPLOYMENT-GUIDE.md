# Deployment Guide

This guide walks you through **installing, configuring, and running**
the TTI-O MCP Server from a fresh machine. It assumes you know how to
open a terminal, run commands, and edit text files, but it does not
assume you've worked with Python packaging, SQLAlchemy, or the Model
Context Protocol before.

If you just want a one-screen cheat sheet, skip to the
[Quickstart](#quickstart). If you want to understand what each piece
actually does before you run it, start with
[What this server is](#what-this-server-is).

---

## Contents

1. [What this server is](#what-this-server-is)
2. [How it works (architecture)](#how-it-works-architecture)
3. [Before you start](#before-you-start)
4. [Quickstart](#quickstart)
5. [Step-by-step install](#step-by-step-install)
6. [Configure the environment](#configure-the-environment)
7. [Bootstrap the catalog (database)](#bootstrap-the-catalog-database)
8. [Connect it to a client](#connect-it-to-a-client)
9. [First end-to-end test](#first-end-to-end-test)
10. [Upgrading to a new version](#upgrading-to-a-new-version)
11. [Deployment options (production-ish)](#deployment-options-production-ish)
12. [Troubleshooting](#troubleshooting)
13. [Uninstall](#uninstall)

---

## What this server is

**TTI-O** is a scientific file format for multi-omics data — mass
spectrometry runs, NMR spectra, identifications, quantifications.
Think of a `.mpgo` file as one self-contained record of a biology
experiment.

**MCP** (Model Context Protocol) is a simple wire protocol invented
so that language-model applications like Claude can call external
tools in a consistent way. Every MCP server exposes a handful of
"tools" — named operations with a known input shape. The client
(Claude, an IDE plugin, a custom script) sends a tool call; the
server does the work and sends back a reply.

**TTI-O MCP Server** is the bridge between those two worlds. It lets
a language model:

- **Catalog** `.mpgo` files — the server reads each file once,
  extracts its key metadata (studies, runs, instruments,
  identifications, quantifications), and saves that metadata in a
  small database. The files themselves stay where they are on disk
  or in cloud storage.
- **Query** that catalog — find all runs with an acquisition mode,
  search identifications by ChEBI ID or compound name, list
  quantifications above an abundance threshold.
- **Fetch spectra** lazily — open a specific file, pull one spectrum,
  downsample it so it fits in a chat response.
- **Encrypt and decrypt** — protect a file's intensity channels with
  AES-256-GCM using a key stored **server-side** so keys never cross
  the chat.
- **Sign and verify** — tamper-detect a file's signal-channel datasets
  with HMAC-SHA256 using a separate server-side key. Signatures are
  embedded in HDF5 attributes on each dataset; re-running
  `ttio_verify_signature` with the same key tells you whether anything
  changed.

The server runs as a small Python program. By default it talks to one
client at a time over **standard input/output** (stdio) — the same
channel that command-line programs use to read and write text. The
client starts `ttio-mcp` as a child process, exchanges messages
with it, and shuts it down when the conversation ends.

## How it works (architecture)

A bird's-eye picture:

```
┌────────────────────────┐        stdio pipe        ┌──────────────────────────┐
│   MCP client           │  ◀──── JSON-RPC ────▶   │   ttio-mcp (this repo) │
│   (Claude, IDE, ...)   │                          │                          │
└────────────────────────┘                          │  ┌────────────────────┐  │
                                                    │  │ MCP server loop    │  │
                                                    │  │  - list_tools      │  │
                                                    │  │  - call_tool       │  │
                                                    │  └─────────┬──────────┘  │
                                                    │            │             │
                                                    │   ┌────────▼─────────┐   │
                                                    │   │ 14 tool handlers │   │
                                                    │   └────────┬─────────┘   │
                                                    │            │             │
                                ┌───────────────────┼────────────┼─────────────┼───────────────────┐
                                │                   │            │             │                   │
                          ┌─────▼─────┐       ┌─────▼──────┐  ┌──▼───────┐  ┌──▼────────────┐      │
                          │ Catalog   │       │ Keyring    │  │ TTI-O   │  │ fsspec        │      │
                          │ (SQLite / │       │ (JSON file │  │ library  │  │ (cloud I/O:   │      │
                          │  Postgres)│       │  on disk)  │  │ (.mpgo   │  │  S3, GCS, …)  │      │
                          └───────────┘       └────────────┘  │  reader) │  └───────────────┘      │
                                                              └──────────┘                         │
                                                                   │                               │
                                                                   ▼                               │
                                                          ┌─────────────────┐                      │
                                                          │ .mpgo files on  │                      │
                                                          │ disk or cloud   │ ◀────────────────────┘
                                                          └─────────────────┘
```

### The players

- **MCP client.** Your chat UI, IDE plugin, or custom script. Starts
  `ttio-mcp` as a subprocess; sends JSON messages down stdin; reads
  replies from stdout.
- **ttio-mcp.** A Python package. Its entry point is `serve()` in
  `src/ttio_mcp/server.py`. It builds an `mcp.server.lowlevel.Server`,
  registers the 14 tool handlers, and runs the stdio loop.
- **Catalog.** A small SQL database (SQLite by default, Postgres
  works too). Seven tables: `users`, `files`, `studies`, `runs`,
  `identifications`, `quantifications`, `provenance_records`. The
  schema is managed by **Alembic** migrations.
- **Keyring.** A JSON file on disk whose path you control via the
  `TTIO_KEYRING_PATH` environment variable. Maps a short `key_id`
  like `"demo"` to key material plus an `algorithm` tag. Two
  algorithms are recognised: `"AES-256-GCM"` (exactly 32 bytes, used
  by encrypt / decrypt / push) and `"hmac-sha256"` (variable-length
  non-empty, used by sign / verify). Each tool pins the algorithm it
  expects, so an AES key cannot be used to sign and an HMAC key
  cannot be used to encrypt.
- **TTI-O library.** The Python package `mpeg-o` does all the real
  `.mpgo` reading, writing, encrypting, decrypting. Our server is
  mostly orchestration — it decides *when* to call TTI-O, and what
  to persist afterwards.
- **fsspec.** The Python filesystem abstraction that lets the same
  code open a local file, an S3 object, a GCS object, or an Azure
  blob. Only used when a URI is remote.

### The catalog pattern

The key design choice: **files stay where they are**. The server
never copies a `.mpgo` into the database. It reads each file once
(when you register it), pulls out the metadata a user would want to
search on, and stores **only that metadata**. The raw spectrum bytes
stay on disk or in cloud storage.

- If the file moves or changes, you re-register it (or call
  `ttio_reverify` to detect drift).
- If the file is deleted out from under the server, the catalog row
  still exists — `ttio_reverify` will flag it as unresolvable.
- If you need an actual spectrum in the chat, `ttio_get_spectrum`
  reopens the file, pulls one spectrum by index, downsamples it to
  fit a chat-sized response, and returns channel arrays plus
  metadata.

Why this matters in practice: registering a 2 GB cloud-hosted `.mpgo`
takes about as long as streaming 2 GB through the server once (to
hash it). After that, every subsequent query is a database lookup —
no re-download, no re-parse.

### What a tool call looks like

Let's trace one example end to end. The MCP client calls
`ttio_get_spectrum` with:

```json
{ "run_id": 7, "spectrum_index": 42 }
```

1. The MCP SDK on our side parses the JSON-RPC request and calls the
   registered `call_tool` handler in `src/ttio_mcp/tools/__init__.py`.
2. That dispatcher looks up the handler by name — here
   `ttio_mcp.tools.get_spectrum.handle` — and checks whether it
   accepts a `keyring` argument. It does, so the dispatcher hands
   over a reference to the loaded keyring.
3. The handler asks the catalog: "What file does run 7 belong to?"
   The database answers with the file's URI (`file:///...` or
   `s3://...`) and some metadata.
4. If the file is marked `encrypted=true`, the handler requires a
   `key_id` argument. Absent one, it returns `key_required`. Present
   one, it resolves the key through the keyring **in-process** and
   calls TTI-O to decrypt in-memory.
5. The handler calls `mpeg_o.SpectralDataset.open(...)` — either with
   a local path or with an fsspec-backed stream for cloud URIs — and
   asks TTI-O for spectrum 42.
6. The handler downsamples arrays past `max_points` (default 1000),
   collects metadata, builds a JSON response, closes the dataset, and
   returns.
7. The dispatcher wraps the result in `{"ok": true, "data": {...}}`
   and hands it to the MCP SDK, which sends it back over stdout.

Every other tool is a variation on this pattern: do a database
lookup, maybe do disk/cloud I/O, return JSON.

### Why the key never crosses the wire

A keyring is a JSON file on the same machine as the server. The MCP
client sends a **short name** like `"demo"` — never the key itself.
The server resolves that name to raw bytes in its own memory, hands
those bytes to TTI-O for encrypt/decrypt, and throws the response
back to the client with only the `key_id` string in it. The client
never sees, handles, or is able to exfiltrate the key bytes through
the MCP protocol.

This is why the keyring file lives on the server host and why you
should not check it into version control.

### What the server does *not* do

- **It does not store spectrum bytes.** Ever. The catalog only holds
  metadata.
- **It does not authenticate users.** The `as_user` parameter is a
  string that must match a pre-provisioned row in the `users` table.
  There is no password, no token, no OIDC. For single-user installs
  this is fine — everything runs as `system`. Multi-user work is
  tracked for a later milestone.
- **It does not expose HTTP.** stdio only. If you need network
  access, run the server inside a remote-access tool (SSH, a
  VS Code Remote session, etc.) or behind an MCP-over-HTTP proxy.
- **It does not encrypt files already in the cloud.**
  `ttio_encrypt_file` / `ttio_decrypt_file` are **local-only** — a
  cloud URI returns `remote_not_supported`. For publishing a fresh
  local file, use `ttio_push_file` with a `key_id` — the file is
  encrypted locally into a temp copy and only the ciphertext is
  uploaded. For an object that already sits in the cloud, the
  workflow is manual: pull it down, encrypt locally, push back.

---

## Before you start

You need, on the machine that will run the server:

| Requirement | How to check |
|---|---|
| **Python 3.11 or 3.12** | `python3 --version` |
| **pip** (comes with Python) | `python3 -m pip --version` |
| **git** | `git --version` |
| **A terminal** | bash, zsh, PowerShell — any of them is fine. |
| **~500 MB free disk** | For the virtual environment and Python wheels. The catalog itself is tiny. |

If you want the server to read cloud files (S3, Google Cloud Storage,
Azure Blob Storage), also install credentials for whichever cloud you
use — the usual `aws configure`, `gcloud auth`, `az login` flows all
work. The server reads credentials through the normal environment
variables and profile files.

If you want to try it against Claude Code specifically, install
Claude Code and run `claude --version` to make sure it's on your
PATH.

---

## Quickstart

For the impatient. This drops a single-user, local-only, unencrypted
install into `~/ttio-mcp`.

```bash
# 1. Clone and enter the repo
git clone https://github.com/DTW-Thalion/TTIO-MCP-Server.git ~/ttio-mcp
cd ~/ttio-mcp

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1

# 3. Install the package and its dev tools
pip install -e ".[dev]"

# 4. Create the catalog database
alembic upgrade head

# 5. Verify the server launches
ttio-mcp <<< '' ; echo "exit code: $?"   # exits 0 on clean EOF

# 6. Wire it into Claude Code
claude mcp add ttio-mcp -- "$(pwd)/.venv/bin/ttio-mcp"
```

You can now ask Claude to call `ttio_register_file`, etc.

The rest of this guide explains each step in detail and covers the
multi-user, cloud, and encryption setups.

---

## Step-by-step install

### 1. Clone the repository

Pick a location where you want the server and its code to live. The
code and the virtual environment together take about 500 MB; the
catalog file is kilobytes unless you register thousands of `.mpgo`s.

```bash
git clone https://github.com/DTW-Thalion/TTIO-MCP-Server.git ~/ttio-mcp
cd ~/ttio-mcp
```

### 2. Create a virtual environment

A virtual environment is a private Python installation just for this
project. It keeps the server's dependencies separate from system
Python so upgrades can't break your OS.

```bash
python3 -m venv .venv
```

Activate it in every shell where you want to run `ttio-mcp` or its
commands:

```bash
source .venv/bin/activate         # macOS / Linux
.venv\Scripts\Activate.ps1        # Windows PowerShell
.venv\Scripts\activate.bat        # Windows cmd.exe
```

Your prompt should now start with `(.venv)` — that's how you know
it's active. Everything that follows assumes the venv is active.

### 3. Install the package

```bash
pip install -e ".[dev]"
```

What this does:

- `-e` makes it an **editable** install — Python imports the source
  tree directly, so edits show up without reinstalling.
- `.[dev]` picks up the `dev` extras from `pyproject.toml` —
  `pytest`, `ruff`, and `mypy`, used for development.
- The `mpeg-o` Python package comes from a git tag
  (`v1.1.1` as of this release); pip resolves it directly from
  GitHub. You'll see it being cloned and built on first install —
  this takes a minute or two depending on your network.

If you want cloud support, install the cloud extra too:

```bash
pip install -e ".[dev,cloud]"
```

This adds `s3fs` and `fsspec`, which the server uses to stream `s3://`
and other remote URIs.

### 4. Verify the tests pass

```bash
pytest -q
```

You should see a line like `115 passed in 10s`. If any test fails, stop
and look at the error — something in your environment is off (wrong
Python version, missing system library, corrupt clone). See
[Troubleshooting](#troubleshooting).

### 5. Verify the linter passes

```bash
ruff check .
```

Should print `All checks passed!`. Again, a failure here indicates an
environment problem, not something for you to fix in the code.

---

## Configure the environment

The server is controlled by **four environment variables**. All of
them are optional — the defaults give you a working single-user,
local-only install — but you'll want to set at least one of them if
you deploy beyond a single developer's laptop.

| Variable | What it controls | Default |
|---|---|---|
| `TTIO_MCP_DB_URL` | Which database holds the catalog. | `sqlite:///ttio_mcp.db` (a file in the current directory) |
| `TTIO_MCP_FSSPEC_KWARGS` | Default options passed to every cloud-filesystem call. | *(none)* |
| `TTIO_KEYRING_PATH` | Path to the JSON file that holds encryption keys. | *(none — encryption tools refuse to run)* |
| `TTIO_MCP_INTAKE_DIR` | Directory where `ttio_launch_uploader` stages files picked by the user. | *(none — the uploader tool refuses to run)* |

Set them **in the shell that launches `ttio-mcp`**. If you're
wiring the server into Claude Code or another client, you'll set
them in the same shell where you run `claude mcp add ...`.

Full reference: [docs/configuration.md](docs/configuration.md).

### Picking a database

For a single user on a laptop, the default SQLite file is fine. It
lives next to the repo, backs up easily (just copy the file), and
needs zero setup.

For a shared server or production use, prefer Postgres. The schema
is identical; only the URL changes:

```bash
export TTIO_MCP_DB_URL="postgresql+psycopg://user:pw@host:5432/ttio_mcp"
```

Create the empty database in Postgres first (`createdb ttio_mcp`),
then run `alembic upgrade head` against the new URL.

### Cloud filesystem defaults

Only relevant if you plan to register cloud URIs. Example for a
private S3 bucket that should use your default AWS credentials:

```bash
export TTIO_MCP_FSSPEC_KWARGS='{"anon": false}'
```

For a MinIO / LocalStack / custom-endpoint S3:

```bash
export TTIO_MCP_FSSPEC_KWARGS='{
  "anon": false,
  "client_kwargs": {"endpoint_url": "https://minio.example:9000"}
}'
```

For a public bucket:

```bash
export TTIO_MCP_FSSPEC_KWARGS='{"anon": true}'
```

Individual tool calls can override any key — so you can have anon-default
and pass `{"anon": false}` for a specific private file.

### Setting up a keyring

Only relevant if you plan to use the encryption tools. Here's the
full recipe.

**Step 1** — pick a path for the keyring file and add it to
`.gitignore` if it's anywhere inside a git repo.

```bash
KEYRING=~/.config/ttio-mcp/keyring.json
mkdir -p "$(dirname "$KEYRING")"
chmod 700 "$(dirname "$KEYRING")"
```

**Step 2** — generate a key and write it into the file.

```bash
NEW_KEY=$(python3 -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())')

cat > "$KEYRING" <<EOF
{
  "keys": {
    "demo": {
      "value": "$NEW_KEY",
      "algorithm": "AES-256-GCM",
      "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
      "description": "first demo key"
    }
  }
}
EOF

chmod 600 "$KEYRING"
```

**Step 3** — point the server at it:

```bash
export TTIO_KEYRING_PATH="$KEYRING"
```

You can add more keys to the same file later — just add another
entry under `"keys"`. Each key has its own `key_id` (the map key),
its own base64 `value`, and an `algorithm` tag.

**Step 4 (optional)** — add an HMAC-SHA256 key for signing. Signing
keys use a different algorithm tag; the server refuses to cross the
streams:

```bash
SIGN_KEY=$(python3 -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())')

python3 - <<PY
import json, pathlib
p = pathlib.Path("$KEYRING")
doc = json.loads(p.read_text())
doc["keys"]["release-signer"] = {
    "value": "$SIGN_KEY",
    "algorithm": "hmac-sha256",
    "description": "HMAC-SHA256 release signing key",
}
p.write_text(json.dumps(doc, indent=2))
PY
```

`hmac-sha256` keys can be any non-empty length — 32 bytes is the
conventional HMAC-SHA256 key size and what TTI-O's own tests use, so
it's a sensible default. `AES-256-GCM` keys must be exactly 32 bytes.

**Do not commit this file anywhere.** If you lose an encryption key,
files encrypted with it cannot be recovered. If you lose a signing
key, you can no longer prove integrity of anything signed with it
(and you also can't sign anything new that verifies against the same
key). If you leak either, treat the protected files as compromised
and re-key.

### Setting up the intake directory

`ttio_launch_uploader` pops a tkinter file-picker on the same
desktop the server is running on (MCP stdio is same-machine by
definition) and copies the chosen file into whatever directory
`TTIO_MCP_INTAKE_DIR` points to. Without that env var set, the tool
refuses to run.

```bash
export TTIO_MCP_INTAKE_DIR="$HOME/mpeg-o/intake"
```

The server auto-creates the directory on first use, so you don't need
`mkdir -p`. A same-name collision (`sample.mpgo` already in intake)
appends a UTC timestamp (`sample-20260424T120000Z.mpgo`); a second
collision tacks on an integer. The original on disk is never touched.

After a file is staged, call `ttio_register_file` against the
returned `destination` to bring it into the catalog — the uploader
itself writes no catalog rows.

**Display required.** The tkinter picker and progress window need an
active display session:
- On Linux, an `$DISPLAY` or `$WAYLAND_DISPLAY`.
- On macOS, the user's logged-in desktop session.
- On Windows, just run the server natively, or run it inside WSL2
  with WSLg (bundled with Windows 10 Build 19044+ and Windows 11).

Headless deployments (SSH-only hosts, containers without an X server)
will get `no_display` back — for those, bypass the uploader and point
`ttio_register_file` straight at your existing file.

---

## Bootstrap the catalog (database)

With environment variables set (or defaulted), initialise the
database:

```bash
alembic upgrade head
```

What just happened:

- Alembic read `alembic.ini` and `migrations/env.py` to find the DB
  URL (via your env var) and the schema definition.
- It created the seven tables.
- It inserted a single row in `users`: `{id: 1, name: "system"}`.
  All future catalog writes default to this user unless you pass an
  explicit `as_user`.
- It recorded the current migration version in a metadata table so
  it knows what to do next time.

You can always inspect what it did:

```bash
sqlite3 ttio_mcp.db ".tables"
sqlite3 ttio_mcp.db "SELECT * FROM users;"
```

To completely reset (destructive — wipes all catalog data):

```bash
alembic downgrade base
```

---

## Connect it to a client

The server doesn't speak a network protocol — an MCP client starts
it as a child process and talks to it over stdin/stdout. Depending
on which client you use, the setup differs slightly.

### Claude Code

```bash
claude mcp add ttio-mcp -- "$(pwd)/.venv/bin/ttio-mcp"
```

Notes:

- Use the **full path** to the `ttio-mcp` binary inside your venv.
  Claude will launch this directly — it won't inherit your shell's
  venv activation.
- Environment variables (`TTIO_MCP_DB_URL` etc.) need to be visible
  **in the process that runs `claude mcp add ...`** so Claude
  captures them. Alternatively, write them into Claude Code's
  settings in `.mcp.json`.
- Verify with `claude mcp list` — you should see `ttio-mcp` and a
  green status.

### Generic MCP client (custom script)

If you're writing your own client using the official `mcp` Python
SDK, start the server as a subprocess and drive it with
`StdioServerParameters`. Minimal example:

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

params = StdioServerParameters(
    command="/home/you/ttio-mcp/.venv/bin/ttio-mcp",
    args=[],
    env={"TTIO_MCP_DB_URL": "sqlite:////tmp/cat.db"},
)

async def run():
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            print([t.name for t in result.tools])
```

### IDE plugins

Most MCP-capable IDE plugins want two things: a **command to run**
and a **set of environment variables**. Point the command at the
same `.venv/bin/ttio-mcp` full path. Everything else is the same
as the Claude Code wire-up.

---

## First end-to-end test

Once the server is connected, you can ask the client to exercise it.
A good smoke test, end to end:

1. Ask the client to call `ttio_list_files` (no arguments). You
   should get `{"total": 0, "limit": 50, "offset": 0, "files": []}` —
   the catalog is empty, which is correct.
2. Pick any `.mpgo` fixture file (the TTI-O repo ships a few under
   `tests/fixtures/`) and ask the client to call
   `ttio_register_file` with its absolute path as `uri`. You should
   get back a `file_id`, counts of studies/runs/identifications, and
   `was_update: false`.
3. Call `ttio_list_files` again — the file is now in the catalog.
4. Call `ttio_get_file` with the `id` you got back — full record.
5. Call `ttio_get_spectrum` with `{run_id, spectrum_index: 0}` — the
   server reopens the file and pulls one spectrum.
6. If you configured a keyring, call `ttio_encrypt_file` with
   `{id, key_id: "demo"}`. The file on disk now has its intensity
   channel ciphered. Call `ttio_get_spectrum` again without a
   `key_id` and you should get `key_required`; pass the same
   `key_id` and you get plaintext back. Finally call
   `ttio_decrypt_file` to restore the file.
7. If you have cloud credentials and want to publish a file, call
   `ttio_push_file` with `{local_uri: "/path/to/local.mpgo",
   remote_uri: "s3://your-bucket/path/sample.mpgo"}` — the server
   streams the bytes up and registers the uploaded object under its
   `s3://` URI. Add `key_id: "demo"` to have the ciphertext land in
   the bucket instead of plaintext.
8. If you added an `hmac-sha256` key to the keyring, call
   `ttio_sign_file` with `{id, key_id: "release-signer"}` — the
   server stamps every `signal_channels/*_values` dataset with an
   HMAC-SHA256 tag. Call `ttio_verify_signature` with the same key
   and you should get `valid: true` plus a per-dataset verdict map.
   Pass a different `key_id` and `valid` flips to `false` without
   raising.
9. If you set `TTIO_MCP_INTAKE_DIR` and the server process can reach
   a display (local desktop, or WSLg on Windows), ask the client to
   call `ttio_launch_uploader`. A file picker opens on your desktop;
   choose any importable file (`.mpgo`, `.mzml`, `.nmrml`, `.imzml`,
   `.mztab`) and watch the progress window stream it into the intake
   directory. The tool returns `{source, destination, format,
   size_bytes}` — follow up with `ttio_register_file` against the
   `destination` to bring the staged file into the catalog.

That's the full round trip. Everything else is filters, pagination,
and edge cases.

### Publishing to the cloud

The server treats cloud encryption in three tiers:

1. **Fresh file, not yet uploaded.** Call `ttio_push_file` with
   `key_id` — a temp copy is encrypted locally, the ciphertext is
   uploaded, the local source is untouched, and the catalog row is
   marked `encrypted=true`. One upload, no wasted bandwidth.
2. **File already in the cloud, needs post-hoc encryption.** Object
   stores are immutable at the object level — there is no "encrypt
   in place" for a remote object. The workflow is manual:
   (a) pull the object down with your cloud client,
   (b) run `ttio_encrypt_file` on the local copy,
   (c) re-upload with `ttio_push_file` (no `key_id`, since the bytes
   are already ciphertext) to a new key.
3. **File already in the cloud, plaintext reads only.** Just
   `ttio_register_file` the `s3://` URI and use it through the query
   tools. Nothing to encrypt.

`ttio_encrypt_file` and `ttio_decrypt_file` refuse cloud URIs with
`remote_not_supported` on purpose — doing it server-side would cost
a download plus an upload per call, and the server has no way to
cache between requests.

### Signing `.mpgo` files

Signing stamps each `signal_channels/*_values` dataset with an
HMAC-SHA256 tag (stored in the `@ttio_signature` HDF5 attribute on
that dataset). Anyone who holds the matching key can later run
`ttio_verify_signature` to confirm that the dataset bytes have not
changed since signing.

Signing is **local-only** and operates on **plaintext** values — a
signed file can be encrypted afterwards for transport, but signing an
already-encrypted file is rejected (`already_encrypted`): the
canonical byte layout HMAC depends on the original plaintext values.

Minimal round-trip:

1. Register the file (local path): `ttio_register_file`.
2. Sign it with an `hmac-sha256` key from the keyring:

   ```json
   {"id": 1, "key_id": "release-signer"}
   ```

   `ttio_sign_file` responds with `signed_datasets: ["/study/.../intensity_values", ...]`
   and fresh `file_sha256` / `content_sha256`.
3. Verify at any later point with the same `key_id`:

   ```json
   {"id": 1, "key_id": "release-signer"}
   ```

   `ttio_verify_signature` returns `valid: true` if every signed
   dataset verifies. Individual verdicts appear in `verified_datasets`
   — if some are `false`, those particular datasets have been
   tampered with (or are being verified with the wrong key).

**Re-signing with a new key.** Call `ttio_sign_file` again with the
new `key_id`; the `@ttio_signature` attribute on each dataset is
overwritten. There's no atomic rotate — file attributes only ever
hold one signature, and it's always the most recent one.

**Signing for cloud distribution.** Sign the local file, then
`ttio_push_file` (with or without encryption) to upload. Verification
against a cloud URI is **not** supported — download the object
locally, re-register, then verify. This keeps the verify path on
byte-stable h5py reads.

**Unsigned files raise a distinct error.** Calling
`ttio_verify_signature` on a file whose datasets carry no
`@ttio_signature` attributes raises `not_signed`, not `valid: false`.
Callers should treat `not_signed` as "no claim to verify" rather than
"failed verification."

---

## Upgrading to a new version

When a new version lands:

```bash
cd ~/ttio-mcp
git pull
source .venv/bin/activate
pip install -e ".[dev]"              # re-resolves dependencies
alembic upgrade head                 # applies any new migrations
pytest -q                            # sanity check
```

Then restart the client — it needs to relaunch `ttio-mcp` to pick
up the new code. In Claude Code that's usually just starting a new
session.

Catalog data is preserved across upgrades. Migrations only add or
adjust tables; they never drop rows.

---

## Deployment options (production-ish)

### Single-user laptop

Everything in the [Quickstart](#quickstart) — SQLite, no keyring, no
cloud — is fine. Back up `ttio_mcp.db` periodically.

### Shared team server

- Move the catalog to Postgres (`TTIO_MCP_DB_URL`). One database
  serves everyone; each user's client launches their own
  `ttio-mcp` subprocess that connects to the shared DB.
- Put the server binary on a path accessible to every user, or let
  each user clone their own copy. The code is stateless — all state
  lives in the database and the keyring.
- Provision a row per real user in the `users` table (via direct
  SQL for now — tooling lands in a later milestone) and pass the
  name through `as_user`.

### Remote / cloud

The server speaks stdio, so a "cloud deployment" usually means one
of:

1. **Run the server on a remote host and expose it via SSH.** The
   client SSHes in and spawns `ttio-mcp` as a remote command. MCP
   over SSH works fine — stdio passes through untouched.
2. **Put it behind an MCP-over-HTTP proxy.** Several community
   proxies exist; they turn stdio into HTTP and back. Consult their
   documentation for auth and TLS.

Either way, make sure:

- The keyring file has restrictive permissions (`chmod 600`, owned
  by the user the server runs as).
- Cloud credentials are loaded from the environment of the user
  running `ttio-mcp`, not from MCP tool arguments.
- `TTIO_MCP_DB_URL` points at a real database with backups. The
  catalog is the only source of truth about which files the server
  has seen — losing it means re-hashing every file on the next
  register call.

---

## Troubleshooting

### "command not found: ttio-mcp"

The virtual environment isn't active, or it's active but in a
different shell. Activate it:

```bash
source ~/ttio-mcp/.venv/bin/activate
```

Or invoke the binary directly:

```bash
~/ttio-mcp/.venv/bin/ttio-mcp
```

### "sqlalchemy.exc.OperationalError: no such table: files"

You skipped the migration. Run:

```bash
alembic upgrade head
```

Then relaunch the client.

### Every register call on a cloud URI fails with `resolve_failed`

Credentials or endpoint config aren't reaching the server. Check:

- Are you running the server in the same shell where you exported
  `AWS_ACCESS_KEY_ID` (or equivalent)? MCP clients launch the
  server as a subprocess and won't forward env vars you set after
  the client started.
- Is `TTIO_MCP_FSSPEC_KWARGS` valid JSON? The server aborts at
  startup if it isn't.
- Does the `cloud` extra actually install in your venv?
  `python -c "import s3fs; print(s3fs.__version__)"` should print a
  version.

### `key_required` when reading an encrypted file

Expected. The catalog has `encrypted=true` for that file. Pass
`key_id` in the `ttio_get_spectrum` call.

### `invalid_keyring`

The JSON keyring file is malformed. Common causes:

- `value` is not base64 (check for stray newlines or quotes).
- The `algorithm` is missing or unknown (only `"AES-256-GCM"` and
  `"hmac-sha256"` are recognised).
- An `AES-256-GCM` entry decodes to something other than 32 bytes.
- An `hmac-sha256` entry decodes to zero bytes.
- The outer structure isn't `{"keys": {...}}`.

Re-generate with the one-liner in [Setting up a keyring](#setting-up-a-keyring).

### `algorithm_mismatch`

A tool was given a `key_id` whose stored algorithm doesn't match the
algorithm the tool requires. Typical triggers:

- Passing an `hmac-sha256` key to `ttio_encrypt_file`,
  `ttio_decrypt_file`, `ttio_push_file`, or `ttio_get_spectrum`
  (these want `AES-256-GCM`).
- Passing an `AES-256-GCM` key to `ttio_sign_file` or
  `ttio_verify_signature` (these want `hmac-sha256`).

Add a key with the right algorithm tag to the keyring and use its
`key_id` instead — keys cannot be used across algorithms.

### `ttio_launch_uploader` returns `intake_not_configured`

You haven't set `TTIO_MCP_INTAKE_DIR` in the shell that launched the
server. MCP clients don't forward env vars you export *after* the
client starts. Kill the client, export the var, relaunch.

### `ttio_launch_uploader` returns `no_display`

The host the server is running on has no display session for tkinter
to open a window against. Typical causes:

- SSH without `-X` / `-Y`.
- A container or CI runner with no X server.
- WSL1 (no GUI layer) — upgrade to WSL2 on Windows 10 Build 19044+
  or Windows 11, which ship WSLg.

The uploader can't run headless by design — it's a human-in-the-loop
tool. For automation, use `ttio_register_file` against a URI you've
already staged by other means.

### `ttio_launch_uploader` returns `cancelled`

The user closed the file-picker without picking a file. Not an
error — just retry when they're ready.

### The client shows "ttio-mcp disconnected" right after starting

The server crashed during startup, probably because an env var
points at something invalid (bad DB URL, bad fsspec JSON). Run
`ttio-mcp` directly from a shell — the traceback will print to
stderr:

```bash
source ~/ttio-mcp/.venv/bin/activate
ttio-mcp
```

Press Ctrl-D to give it EOF and let it exit cleanly.

### Tests fail on a fresh clone

Most commonly: the git-based `mpeg-o` dependency couldn't clone
(firewall, no internet, GitHub down). Run

```bash
pip install --force-reinstall "mpeg-o @ git+https://github.com/DTW-Thalion/TTI-O.git@v1.1.1#subdirectory=python"
```

and watch the output for the actual error.

---

## Uninstall

Clean removal:

```bash
# 1. Remove from your MCP client (Claude Code example)
claude mcp remove ttio-mcp

# 2. Delete the repo (this deletes the venv and the default SQLite catalog)
rm -rf ~/ttio-mcp

# 3. If you exported env vars in your shell profile, remove them
# Edit ~/.bashrc / ~/.zshrc / PowerShell profile and delete the TTIO_* lines

# 4. If you created a keyring outside the repo, delete it
rm -f ~/.config/ttio-mcp/keyring.json

# 5. If you configured an intake dir outside the repo, delete it
rm -rf ~/mpeg-o-intake
```

If you used Postgres, drop the catalog database separately:

```bash
dropdb ttio_mcp
```

That's everything. The TTI-O MCP Server is self-contained to those
paths — nothing else is installed system-wide, nothing else touches
system registries.

---

## Where to go next

- [README.md](README.md) — project summary and milestone status.
- [docs/tools.md](docs/tools.md) — reference for every MCP tool.
- [docs/configuration.md](docs/configuration.md) — full env-var reference.
- [CHANGELOG.md](CHANGELOG.md) — what changed in each release.
- Per-milestone handoffs (`HANDOFF*.md`) if you want the historical
  design decisions behind each milestone.
