# Deployment Guide

This guide walks you through **installing, configuring, and running**
the MPEG-O MCP Server from a fresh machine. It assumes you know how to
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

**MPEG-O** is a scientific file format for multi-omics data — mass
spectrometry runs, NMR spectra, identifications, quantifications.
Think of a `.mpgo` file as one self-contained record of a biology
experiment.

**MCP** (Model Context Protocol) is a simple wire protocol invented
so that language-model applications like Claude can call external
tools in a consistent way. Every MCP server exposes a handful of
"tools" — named operations with a known input shape. The client
(Claude, an IDE plugin, a custom script) sends a tool call; the
server does the work and sends back a reply.

**MPEG-O MCP Server** is the bridge between those two worlds. It lets
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

The server runs as a small Python program. By default it talks to one
client at a time over **standard input/output** (stdio) — the same
channel that command-line programs use to read and write text. The
client starts `mpeg-o-mcp` as a child process, exchanges messages
with it, and shuts it down when the conversation ends.

## How it works (architecture)

A bird's-eye picture:

```
┌────────────────────────┐        stdio pipe        ┌──────────────────────────┐
│   MCP client           │  ◀──── JSON-RPC ────▶   │   mpeg-o-mcp (this repo) │
│   (Claude, IDE, ...)   │                          │                          │
└────────────────────────┘                          │  ┌────────────────────┐  │
                                                    │  │ MCP server loop    │  │
                                                    │  │  - list_tools      │  │
                                                    │  │  - call_tool       │  │
                                                    │  └─────────┬──────────┘  │
                                                    │            │             │
                                                    │   ┌────────▼─────────┐   │
                                                    │   │ 10 tool handlers │   │
                                                    │   └────────┬─────────┘   │
                                                    │            │             │
                                ┌───────────────────┼────────────┼─────────────┼───────────────────┐
                                │                   │            │             │                   │
                          ┌─────▼─────┐       ┌─────▼──────┐  ┌──▼───────┐  ┌──▼────────────┐      │
                          │ Catalog   │       │ Keyring    │  │ MPEG-O   │  │ fsspec        │      │
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
  `mpeg-o-mcp` as a subprocess; sends JSON messages down stdin; reads
  replies from stdout.
- **mpeg-o-mcp.** A Python package. Its entry point is `serve()` in
  `src/mpeg_o_mcp/server.py`. It builds an `mcp.server.lowlevel.Server`,
  registers the 10 tool handlers, and runs the stdio loop.
- **Catalog.** A small SQL database (SQLite by default, Postgres
  works too). Seven tables: `users`, `files`, `studies`, `runs`,
  `identifications`, `quantifications`, `provenance_records`. The
  schema is managed by **Alembic** migrations.
- **Keyring.** A JSON file on disk whose path you control via the
  `MPGO_KEYRING_PATH` environment variable. Maps a short `key_id`
  like `"demo"` to 32 bytes of AES-256-GCM key material.
- **MPEG-O library.** The Python package `mpeg-o` does all the real
  `.mpgo` reading, writing, encrypting, decrypting. Our server is
  mostly orchestration — it decides *when* to call MPEG-O, and what
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
  `mpgo_reverify` to detect drift).
- If the file is deleted out from under the server, the catalog row
  still exists — `mpgo_reverify` will flag it as unresolvable.
- If you need an actual spectrum in the chat, `mpgo_get_spectrum`
  reopens the file, pulls one spectrum by index, downsamples it to
  fit a chat-sized response, and returns channel arrays plus
  metadata.

Why this matters in practice: registering a 2 GB cloud-hosted `.mpgo`
takes about as long as streaming 2 GB through the server once (to
hash it). After that, every subsequent query is a database lookup —
no re-download, no re-parse.

### What a tool call looks like

Let's trace one example end to end. The MCP client calls
`mpgo_get_spectrum` with:

```json
{ "run_id": 7, "spectrum_index": 42 }
```

1. The MCP SDK on our side parses the JSON-RPC request and calls the
   registered `call_tool` handler in `src/mpeg_o_mcp/tools/__init__.py`.
2. That dispatcher looks up the handler by name — here
   `mpeg_o_mcp.tools.get_spectrum.handle` — and checks whether it
   accepts a `keyring` argument. It does, so the dispatcher hands
   over a reference to the loaded keyring.
3. The handler asks the catalog: "What file does run 7 belong to?"
   The database answers with the file's URI (`file:///...` or
   `s3://...`) and some metadata.
4. If the file is marked `encrypted=true`, the handler requires a
   `key_id` argument. Absent one, it returns `key_required`. Present
   one, it resolves the key through the keyring **in-process** and
   calls MPEG-O to decrypt in-memory.
5. The handler calls `mpeg_o.SpectralDataset.open(...)` — either with
   a local path or with an fsspec-backed stream for cloud URIs — and
   asks MPEG-O for spectrum 42.
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
those bytes to MPEG-O for encrypt/decrypt, and throws the response
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
- **It does not encrypt cloud files.** Encrypt/decrypt are
  **local-only** — a cloud URI returns `remote_not_supported`. If
  you need to encrypt a cloud file, download, encrypt, re-upload
  manually for now.

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
install into `~/mpeg-o-mcp`.

```bash
# 1. Clone and enter the repo
git clone https://github.com/DTW-Thalion/MPEG-O-MCP-Server.git ~/mpeg-o-mcp
cd ~/mpeg-o-mcp

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1

# 3. Install the package and its dev tools
pip install -e ".[dev]"

# 4. Create the catalog database
alembic upgrade head

# 5. Verify the server launches
mpeg-o-mcp <<< '' ; echo "exit code: $?"   # exits 0 on clean EOF

# 6. Wire it into Claude Code
claude mcp add mpeg-o-mcp -- "$(pwd)/.venv/bin/mpeg-o-mcp"
```

You can now ask Claude to call `mpgo_register_file`, etc.

The rest of this guide explains each step in detail and covers the
multi-user, cloud, and encryption setups.

---

## Step-by-step install

### 1. Clone the repository

Pick a location where you want the server and its code to live. The
code and the virtual environment together take about 500 MB; the
catalog file is kilobytes unless you register thousands of `.mpgo`s.

```bash
git clone https://github.com/DTW-Thalion/MPEG-O-MCP-Server.git ~/mpeg-o-mcp
cd ~/mpeg-o-mcp
```

### 2. Create a virtual environment

A virtual environment is a private Python installation just for this
project. It keeps the server's dependencies separate from system
Python so upgrades can't break your OS.

```bash
python3 -m venv .venv
```

Activate it in every shell where you want to run `mpeg-o-mcp` or its
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

You should see a line like `68 passed in 6s`. If any test fails, stop
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

The server is controlled by **three environment variables**. All of
them are optional — the defaults give you a working single-user,
local-only install — but you'll want to set at least one of them if
you deploy beyond a single developer's laptop.

| Variable | What it controls | Default |
|---|---|---|
| `MPGO_MCP_DB_URL` | Which database holds the catalog. | `sqlite:///mpeg_o_mcp.db` (a file in the current directory) |
| `MPGO_MCP_FSSPEC_KWARGS` | Default options passed to every cloud-filesystem call. | *(none)* |
| `MPGO_KEYRING_PATH` | Path to the JSON file that holds encryption keys. | *(none — encryption tools refuse to run)* |

Set them **in the shell that launches `mpeg-o-mcp`**. If you're
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
export MPGO_MCP_DB_URL="postgresql+psycopg://user:pw@host:5432/mpeg_o_mcp"
```

Create the empty database in Postgres first (`createdb mpeg_o_mcp`),
then run `alembic upgrade head` against the new URL.

### Cloud filesystem defaults

Only relevant if you plan to register cloud URIs. Example for a
private S3 bucket that should use your default AWS credentials:

```bash
export MPGO_MCP_FSSPEC_KWARGS='{"anon": false}'
```

For a MinIO / LocalStack / custom-endpoint S3:

```bash
export MPGO_MCP_FSSPEC_KWARGS='{
  "anon": false,
  "client_kwargs": {"endpoint_url": "https://minio.example:9000"}
}'
```

For a public bucket:

```bash
export MPGO_MCP_FSSPEC_KWARGS='{"anon": true}'
```

Individual tool calls can override any key — so you can have anon-default
and pass `{"anon": false}` for a specific private file.

### Setting up a keyring

Only relevant if you plan to use the encryption tools. Here's the
full recipe.

**Step 1** — pick a path for the keyring file and add it to
`.gitignore` if it's anywhere inside a git repo.

```bash
KEYRING=~/.config/mpeg-o-mcp/keyring.json
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
export MPGO_KEYRING_PATH="$KEYRING"
```

You can add more keys to the same file later — just add another
entry under `"keys"`. Each key has its own `key_id` (the map key),
its own base64 `value`, and the same `algorithm: "AES-256-GCM"`.

**Do not commit this file anywhere.** If you lose the key, files
encrypted with it cannot be recovered. If you leak it, anyone who
has it can decrypt those files.

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
sqlite3 mpeg_o_mcp.db ".tables"
sqlite3 mpeg_o_mcp.db "SELECT * FROM users;"
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
claude mcp add mpeg-o-mcp -- "$(pwd)/.venv/bin/mpeg-o-mcp"
```

Notes:

- Use the **full path** to the `mpeg-o-mcp` binary inside your venv.
  Claude will launch this directly — it won't inherit your shell's
  venv activation.
- Environment variables (`MPGO_MCP_DB_URL` etc.) need to be visible
  **in the process that runs `claude mcp add ...`** so Claude
  captures them. Alternatively, write them into Claude Code's
  settings in `.mcp.json`.
- Verify with `claude mcp list` — you should see `mpeg-o-mcp` and a
  green status.

### Generic MCP client (custom script)

If you're writing your own client using the official `mcp` Python
SDK, start the server as a subprocess and drive it with
`StdioServerParameters`. Minimal example:

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

params = StdioServerParameters(
    command="/home/you/mpeg-o-mcp/.venv/bin/mpeg-o-mcp",
    args=[],
    env={"MPGO_MCP_DB_URL": "sqlite:////tmp/cat.db"},
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
same `.venv/bin/mpeg-o-mcp` full path. Everything else is the same
as the Claude Code wire-up.

---

## First end-to-end test

Once the server is connected, you can ask the client to exercise it.
A good smoke test, end to end:

1. Ask the client to call `mpgo_list_files` (no arguments). You
   should get `{"total": 0, "limit": 50, "offset": 0, "files": []}` —
   the catalog is empty, which is correct.
2. Pick any `.mpgo` fixture file (the MPEG-O repo ships a few under
   `tests/fixtures/`) and ask the client to call
   `mpgo_register_file` with its absolute path as `uri`. You should
   get back a `file_id`, counts of studies/runs/identifications, and
   `was_update: false`.
3. Call `mpgo_list_files` again — the file is now in the catalog.
4. Call `mpgo_get_file` with the `id` you got back — full record.
5. Call `mpgo_get_spectrum` with `{run_id, spectrum_index: 0}` — the
   server reopens the file and pulls one spectrum.
6. If you configured a keyring, call `mpgo_encrypt_file` with
   `{id, key_id: "demo"}`. The file on disk now has its intensity
   channel ciphered. Call `mpgo_get_spectrum` again without a
   `key_id` and you should get `key_required`; pass the same
   `key_id` and you get plaintext back. Finally call
   `mpgo_decrypt_file` to restore the file.

That's the full round trip. Everything else is filters, pagination,
and edge cases.

---

## Upgrading to a new version

When a new version lands:

```bash
cd ~/mpeg-o-mcp
git pull
source .venv/bin/activate
pip install -e ".[dev]"              # re-resolves dependencies
alembic upgrade head                 # applies any new migrations
pytest -q                            # sanity check
```

Then restart the client — it needs to relaunch `mpeg-o-mcp` to pick
up the new code. In Claude Code that's usually just starting a new
session.

Catalog data is preserved across upgrades. Migrations only add or
adjust tables; they never drop rows.

---

## Deployment options (production-ish)

### Single-user laptop

Everything in the [Quickstart](#quickstart) — SQLite, no keyring, no
cloud — is fine. Back up `mpeg_o_mcp.db` periodically.

### Shared team server

- Move the catalog to Postgres (`MPGO_MCP_DB_URL`). One database
  serves everyone; each user's client launches their own
  `mpeg-o-mcp` subprocess that connects to the shared DB.
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
   client SSHes in and spawns `mpeg-o-mcp` as a remote command. MCP
   over SSH works fine — stdio passes through untouched.
2. **Put it behind an MCP-over-HTTP proxy.** Several community
   proxies exist; they turn stdio into HTTP and back. Consult their
   documentation for auth and TLS.

Either way, make sure:

- The keyring file has restrictive permissions (`chmod 600`, owned
  by the user the server runs as).
- Cloud credentials are loaded from the environment of the user
  running `mpeg-o-mcp`, not from MCP tool arguments.
- `MPGO_MCP_DB_URL` points at a real database with backups. The
  catalog is the only source of truth about which files the server
  has seen — losing it means re-hashing every file on the next
  register call.

---

## Troubleshooting

### "command not found: mpeg-o-mcp"

The virtual environment isn't active, or it's active but in a
different shell. Activate it:

```bash
source ~/mpeg-o-mcp/.venv/bin/activate
```

Or invoke the binary directly:

```bash
~/mpeg-o-mcp/.venv/bin/mpeg-o-mcp
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
- Is `MPGO_MCP_FSSPEC_KWARGS` valid JSON? The server aborts at
  startup if it isn't.
- Does the `cloud` extra actually install in your venv?
  `python -c "import s3fs; print(s3fs.__version__)"` should print a
  version.

### `key_required` when reading an encrypted file

Expected. The catalog has `encrypted=true` for that file. Pass
`key_id` in the `mpgo_get_spectrum` call.

### `invalid_keyring`

The JSON keyring file is malformed. Common causes:

- `value` is not base64 (check for stray newlines or quotes).
- The decoded key isn't exactly 32 bytes.
- `algorithm` is present but not `"AES-256-GCM"`.
- The outer structure isn't `{"keys": {...}}`.

Re-generate with the one-liner in [Setting up a keyring](#setting-up-a-keyring).

### The client shows "mpeg-o-mcp disconnected" right after starting

The server crashed during startup, probably because an env var
points at something invalid (bad DB URL, bad fsspec JSON). Run
`mpeg-o-mcp` directly from a shell — the traceback will print to
stderr:

```bash
source ~/mpeg-o-mcp/.venv/bin/activate
mpeg-o-mcp
```

Press Ctrl-D to give it EOF and let it exit cleanly.

### Tests fail on a fresh clone

Most commonly: the git-based `mpeg-o` dependency couldn't clone
(firewall, no internet, GitHub down). Run

```bash
pip install --force-reinstall "mpeg-o @ git+https://github.com/DTW-Thalion/MPEG-O.git@v1.1.1#subdirectory=python"
```

and watch the output for the actual error.

---

## Uninstall

Clean removal:

```bash
# 1. Remove from your MCP client (Claude Code example)
claude mcp remove mpeg-o-mcp

# 2. Delete the repo (this deletes the venv and the default SQLite catalog)
rm -rf ~/mpeg-o-mcp

# 3. If you exported env vars in your shell profile, remove them
# Edit ~/.bashrc / ~/.zshrc / PowerShell profile and delete the MPGO_* lines

# 4. If you created a keyring outside the repo, delete it
rm -f ~/.config/mpeg-o-mcp/keyring.json
```

If you used Postgres, drop the catalog database separately:

```bash
dropdb mpeg_o_mcp
```

That's everything. The MPEG-O MCP Server is self-contained to those
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
