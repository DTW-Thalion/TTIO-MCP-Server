# Deployment Guide

This guide walks you through **installing, configuring, and running**
the TTI-O MCP Server from a fresh machine. It assumes you know how to
open a terminal, run commands, and edit text files, but it does not
assume you've worked with Python packaging or the Model Context
Protocol before.

If you just want a one-screen cheat sheet, skip to the
[Quickstart](#quickstart). If you want to understand what each piece
actually does before you run it, start with
[What this server is](#what-this-server-is).

> **Version note.** This guide describes **v0.9.0**, the workbench-client
> rewrite. In this release the server is a **non-admin client of a running
> [tti-workbench-server](https://github.com/DTW-Thalion/tti-workbench-server)**
> — it holds a session token in memory and relays calls to that server. It
> does **not** maintain a local file catalog, a database, or a keyring.
> (Pre-0.9.0 releases were a local `.mpgo` catalog; if you are following
> instructions that mention `alembic`, a SQLite catalog, or
> `ttio_register_file`, they are for the old architecture.)

---

## Contents

1. [What this server is](#what-this-server-is)
2. [How it works (architecture)](#how-it-works-architecture)
3. [Before you start](#before-you-start)
4. [Quickstart](#quickstart)
5. [Step-by-step install](#step-by-step-install)
6. [Configure the environment](#configure-the-environment)
7. [Authenticate](#authenticate)
8. [Connect it to a client](#connect-it-to-a-client)
9. [First end-to-end test](#first-end-to-end-test)
10. [Upgrading to a new version](#upgrading-to-a-new-version)
11. [Deployment options](#deployment-options)
12. [Troubleshooting](#troubleshooting)
13. [Uninstall](#uninstall)

---

## What this server is

**TTI-O** is a scientific file format for multi-omics data — mass
spectrometry runs, NMR spectra, identifications, quantifications. A
`.tio` file is one self-contained record of a biology experiment.

**tti-workbench-server** is the long-running service that actually
stores those containers, runs cohort queries, schedules pipeline jobs,
manages interactive sessions, and brokers encrypted transfers. It
speaks an HTTP + WebSocket API on (by default) port 18443 and requires
authentication.

**MCP** (Model Context Protocol) is a wire protocol that lets
language-model applications like Claude call external tools in a
consistent way. Every MCP server exposes a set of "tools" — named
operations with a known input shape. The client (Claude, an IDE plugin,
a custom script) sends a tool call; the server does the work and sends
back a reply.

**TTI-O MCP Server** (`ttio-mcp`) is the bridge between those two
worlds. It logs in to a workbench server as a regular **non-admin
user** and exposes that user's capabilities to an LLM as 28 MCP tools:

- **Auth** — `ttio_login`, `ttio_whoami`, `ttio_logout`,
  `ttio_connection_status`.
- **Containers** — list, get, inspect layers, read the HDF5 manifest.
- **Cohorts** — run cohort queries and preview their row counts.
- **Jobs / Pipelines** — submit, list, get, cancel jobs; tail job
  events; list and get pipelines.
- **Sessions** — create, list, get, terminate interactive sessions;
  fetch a session attach URL.
- **Transfers** — upload and download containers (`plain`, `byok`,
  `server-kek`, `pqc` encryption modes); list federation peers.
- **Data** — summarize, read, and export a **local** `.tio` file (e.g.
  one fetched via `ttio_download`).

It deliberately exposes **no admin or destructive operations** — no user
management, no groups, no operations dashboard, no KEK rotation, no
pipeline registration, and no container delete. Whatever the workbench
account you log in with is allowed to do, the LLM can do through these
tools; nothing more.

The server runs as a small Python program. It talks to **one MCP client
at a time over standard input/output** (stdio). The client starts
`ttio-mcp` as a child process, exchanges JSON-RPC messages with it, and
shuts it down when the conversation ends. There is **no network port,
no HTTP endpoint, and no SSE** on the MCP side — the only network the
server itself opens is the outbound connection to the workbench.

## How it works (architecture)

A bird's-eye picture:

```
┌────────────────────────┐     stdio (JSON-RPC)     ┌──────────────────────────┐      HTTPS / WSS      ┌────────────────────────┐
│   MCP client           │  ◀────────────────────▶ │   ttio-mcp (this repo)   │  ◀─────────────────▶  │  tti-workbench-server  │
│   (Claude, IDE, ...)   │      stdin / stdout      │                          │   Bearer / WS token   │  (REST + WebSocket on  │
└────────────────────────┘                          │  ┌────────────────────┐  │                       │   :18443; the real     │
                                                    │  │ FastMCP server     │  │                       │   data + compute)      │
                                                    │  │  - 28 tools        │  │                       └────────────────────────┘
                                                    │  └─────────┬──────────┘  │
                                                    │  ┌─────────▼──────────┐  │
                                                    │  │ ConnectionManager  │  │
                                                    │  │  (one in-memory    │  │
                                                    │  │   WorkbenchClient) │  │
                                                    │  └────────────────────┘  │
                                                    └──────────────────────────┘
```

### The players

- **MCP client.** Your chat UI, IDE plugin, or custom script. Starts
  `ttio-mcp` as a subprocess; sends JSON messages down stdin; reads
  replies from stdout.
- **ttio-mcp.** A Python package built on **FastMCP**. Its entry point
  is `main()` in `src/ttio_mcp/server.py`, exposed as the `ttio-mcp`
  console script. On startup it builds the FastMCP app, registers the
  seven tool modules, and — if a URL and token are configured —
  pre-connects to the workbench.
- **ConnectionManager.** Owns at most one authenticated
  `ttio.workbench.WorkbenchClient`. The session token lives **in memory
  only** and is never written to disk. There is no catalog, no database,
  and no keyring in this server.
- **tti-workbench-server.** The separate long-running service that holds
  the actual containers, runs queries and jobs, and brokers transfers.
  `ttio-mcp` is just one of its clients (the workbench's own browser UI,
  `tio-browser`, is another). You must have a reachable workbench
  instance for this server to do anything.
- **ttio SDK.** The Python package `ttio` (installed automatically as a
  dependency) provides the `ttio.workbench` client used to talk to the
  server, plus the `ttio.SpectralDataset` reader the Data tools use on
  local `.tio` files.

### What a tool call looks like

Let's trace one example end to end. The MCP client calls
`ttio_containers_list` with `{"project": "demo", "limit": 50}`:

1. FastMCP parses the JSON-RPC request and dispatches to the registered
   handler in `src/ttio_mcp/tools/containers.py`.
2. The handler asks the `ConnectionManager` for the active client. If
   nobody has logged in (and no headless token is configured), it
   returns an error telling the LLM to call `ttio_login`.
3. The handler calls `client.containers().list(...)` on the
   `WorkbenchClient`, which issues an authenticated HTTPS request to the
   workbench on the user's behalf.
4. The workbench returns a page of container rows. The handler shapes
   them into a compact dict (`containers[]`, `next_cursor`, `has_more`)
   and returns it.
5. FastMCP serialises the dict to JSON text and sends it back over
   stdout.

Every other tool is a variation on this: check the session, call the
`ttio.workbench` client, shape the response. The **Data** tools are the
exception — they read a local `.tio` file directly via
`ttio.SpectralDataset` and do **not** require an active workbench
session.

### Why no secret ever crosses the MCP wire

The MCP client sends a **username, password, and TOTP code** to
`ttio_login` (or you configure an API key in the *server's* environment
for headless use). The resulting session token is held in the
`ConnectionManager`'s memory for the life of the process and is never
echoed back to the client, never persisted to disk, and never passed as
a tool result. `ttio_logout` drops it; killing the process drops it.

### What the server does *not* do

- **It does not store data.** There is no catalog and no database. All
  state lives on the workbench server; `ttio-mcp` is stateless between
  process restarts apart from the in-memory session.
- **It does not expose admin or destructive tools.** User management,
  groups, operations dashboard, KEK rotation, pipeline registration, and
  container delete are all intentionally absent. See the "Not exposed"
  section of [docs/tools.md](docs/tools.md).
- **It does not expose HTTP or SSE.** The MCP transport is stdio only.
  If you need an LLM on a different machine to use it, run `ttio-mcp` on
  the *same* machine as the MCP client and let *that* machine reach the
  workbench over the network (see [Deployment options](#deployment-options)).

---

## Before you start

You need, on the machine that will run the server (which is the same
machine that runs the MCP client):

| Requirement | How to check |
|---|---|
| **Python 3.11 or 3.12** | `python3 --version` |
| **pip** (comes with Python) | `python3 -m pip --version` |
| **git** | `git --version` |
| **A C toolchain** | `cc --version` — the `ttio` SDK is built from source on install (`build-essential` on Debian/Ubuntu, the Xcode Command Line Tools on macOS). |
| **A terminal** | bash, zsh, PowerShell — any of them. |
| **A reachable tti-workbench-server (v1.1.0+)** | You should be able to open its URL (e.g. `https://wb.example.com:18443`) and have credentials (a username + password + TOTP, or an API key). |
| **~500 MB free disk** | For the virtual environment and Python wheels. |

If you want to try it against Claude Code specifically, install Claude
Code and run `claude --version` to make sure it's on your PATH.

You do **not** need a database, a cloud account, or a keyring for this
server — those belonged to the old architecture.

---

## Quickstart

For the impatient. This installs `ttio-mcp` and wires it into Claude
Code, pointed at a workbench you can already reach.

```bash
# 1. Install the pinned release (builds the ttio SDK from source)
pip install "ttio-mcp @ git+https://github.com/DTW-Thalion/TTIO-MCP-Server.git@v0.9.0"

# 2. Point it at your workbench
export TTIO_WB_URL="https://wb.example.com:18443"

# 3. Wire it into Claude Code
claude mcp add ttio-mcp -- ttio-mcp
```

Then, from the LLM, call `ttio_login` with your username, password, and
current TOTP code — or set `TTIO_WB_TOKEN` (an API key) before step 3 to
auto-connect with no login call.

The rest of this guide explains each step in detail and covers headless,
multi-user, and encrypted-transfer setups.

---

## Step-by-step install

### Option A — pip install the pinned release (recommended)

`ttio-mcp` is **not** published to PyPI; install it straight from the
tagged GitHub release:

```bash
pip install "ttio-mcp @ git+https://github.com/DTW-Thalion/TTIO-MCP-Server.git@v0.9.0"
```

This installs the `ttio-mcp` console script and pulls its pinned
`ttio[network,crypto]` dependency. Because `ttio` is built from source,
the install needs `git` and a C toolchain (see [Before you
start](#before-you-start)); the first install takes a minute or two.

To enable the optional transfer extras, request them by name:

```bash
# post-quantum transfers (pqc, ML-KEM-1024) and/or remote-.tio URLs (cloud)
pip install "ttio-mcp[pqc] @ git+https://github.com/DTW-Thalion/TTIO-MCP-Server.git@v0.9.0"
```

> Installing into a **virtual environment** is strongly recommended so
> the server's dependencies don't collide with system Python:
>
> ```bash
> python3 -m venv ~/ttio-mcp-venv
> source ~/ttio-mcp-venv/bin/activate     # Windows: ~\ttio-mcp-venv\Scripts\Activate.ps1
> pip install "ttio-mcp @ git+https://github.com/DTW-Thalion/TTIO-MCP-Server.git@v0.9.0"
> ```
>
> If you use a venv, the console script lives at
> `~/ttio-mcp-venv/bin/ttio-mcp` — use that **full path** when you wire
> the server into a client, because the client won't inherit your shell's
> venv activation.

### Option B — from a clone (development)

```bash
git clone https://github.com/DTW-Thalion/TTIO-MCP-Server.git
cd TTIO-MCP-Server
git checkout v0.9.0
python3 -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### Verify the install

```bash
pytest -q          # expect: 55 passed, 12 skipped
ruff check src tests
```

The 12 skipped tests are the opt-in live integration suite; they only
run against a real workbench (see [First end-to-end
test](#first-end-to-end-test)). A failure in the other 55 means your
environment is off (wrong Python version, missing C toolchain, the
`ttio` SDK couldn't build) — see [Troubleshooting](#troubleshooting).

---

## Configure the environment

The server is controlled entirely by **environment variables**, read
from the process that launches `ttio-mcp`. No secrets are ever accepted
through MCP tool arguments, and nothing is written to disk.

| Variable | What it controls | Default |
|---|---|---|
| `TTIO_WB_URL` | Workbench server URL, e.g. `https://wb.example.com:18443` or `wss://wb.example.com:18443/transport`. Required for headless auto-connect; may also be passed per-call to `ttio_login`. | *(unset)* |
| `TTIO_WB_TOKEN` | API key (`ttiowbk_...`) or bearer token (`ttiowbs_...`) for headless auto-connect at startup. | *(unset)* |
| `TTIO_WB_USERNAME` | Optional username label attached to a headless session (informational only). | *(unset)* |
| `TTIO_MCP_EXPORT_DIR` | Directory where `ttio_dataset_export` writes parquet/csv/json output. | `~/.local/state/ttio-mcp/exports` |
| `TTIO_MCP_CACHE_DIR` | Directory for intermediate cache files. | `~/.local/state/ttio-mcp/cache` |
| `TTIO_MCP_PAGE_SIZE` | Default page size for container-list calls when the caller omits `limit`. | `100` |

> **Where to set them.** MCP clients launch `ttio-mcp` as a subprocess
> and capture the environment **at the moment the client starts it** —
> they do **not** forward variables you export later. So set these in the
> same shell that runs `claude mcp add ...`, or bake them into the
> client's MCP config (see [Connect it to a
> client](#connect-it-to-a-client)).

Full reference: [docs/configuration.md](docs/configuration.md).

### Export and cache directories

Both default under `~/.local/state/ttio-mcp/` (honouring `XDG_STATE_HOME`
if set) and are created on first use. Point them somewhere else if you
want exports collected in a known place or caches on a faster disk:

```bash
export TTIO_MCP_EXPORT_DIR="$HOME/ttio-exports"
export TTIO_MCP_CACHE_DIR="/var/cache/ttio-mcp"
```

Callers can override the export directory per call with the `out_dir`
parameter on `ttio_dataset_export`.

---

## Authenticate

There are two ways to establish a workbench session. Tokens live in the
server process's memory only and are never written to disk.

### Interactive login (recommended for desktop use)

Leave `TTIO_WB_TOKEN` unset. After the server starts, call `ttio_login`
from the LLM client with a username, password, and the current 6-digit
TOTP code:

```jsonc
// tool call: ttio_login
{
  "username": "alice",
  "password": "hunter2",
  "totp": "123456",
  "url": "https://wb.example.com:18443"   // optional; defaults to TTIO_WB_URL
}
```

The session token expires after roughly 24 hours; call `ttio_login`
again to refresh it. `ttio_logout` drops the in-memory session.

### Headless / API-key auto-connect (recommended for unattended use)

Set both `TTIO_WB_URL` and `TTIO_WB_TOKEN` before launching `ttio-mcp`.
The server establishes a session at startup and no `ttio_login` call is
needed:

```bash
export TTIO_WB_URL="https://wb.example.com:18443"
export TTIO_WB_TOKEN="ttiowbk_abc123..."
ttio-mcp
```

- **API keys** (`ttiowbk_...`) are issued by a workbench administrator
  from the Operations Dashboard. They do not expire on their own but can
  be revoked server-side — the right choice for unattended deployments.
- **Bearer tokens** (`ttiowbs_...`) are short-lived session tokens from a
  prior login and are less suitable for headless use.

If auto-connect fails (bad URL, unreachable server, revoked key), the
server still starts; `ttio_connection_status` reports the disconnected
state and you can recover by calling `ttio_login`.

Check status at any time with `ttio_connection_status` or `ttio_whoami`.

---

## Connect it to a client

The server speaks stdio — an MCP client starts it as a child process and
talks over stdin/stdout. Setup differs slightly per client.

### Claude Code

```bash
claude mcp add ttio-mcp -- ttio-mcp
```

If you installed into a virtual environment, use the **full path** to
the console script so Claude can launch it without your venv activated:

```bash
claude mcp add ttio-mcp -- /home/you/ttio-mcp-venv/bin/ttio-mcp
```

Notes:

- Environment variables (`TTIO_WB_URL`, `TTIO_WB_TOKEN`, …) must be
  visible **in the shell that runs `claude mcp add ...`** so Claude
  captures them, or written into Claude Code's MCP config. To set them
  inline:

  ```bash
  claude mcp add ttio-mcp \
    --env TTIO_WB_URL=https://wb.example.com:18443 \
    --env TTIO_WB_TOKEN=ttiowbk_abc123... \
    -- ttio-mcp
  ```

- Verify with `claude mcp list` — you should see `ttio-mcp` with a green
  status.

### Claude Desktop / other JSON-config clients

Clients that use a JSON config file (e.g. `claude_desktop_config.json`,
or a project `.mcp.json`) take a command, args, and an env map:

```jsonc
{
  "mcpServers": {
    "ttio-mcp": {
      "command": "/home/you/ttio-mcp-venv/bin/ttio-mcp",
      "args": [],
      "env": {
        "TTIO_WB_URL": "https://wb.example.com:18443",
        "TTIO_WB_TOKEN": "ttiowbk_abc123..."
      }
    }
  }
}
```

Restart the client after editing the file.

### Generic MCP client (custom script)

Using the official `mcp` Python SDK, start the server as a subprocess
and drive it with `StdioServerParameters`:

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

params = StdioServerParameters(
    command="/home/you/ttio-mcp-venv/bin/ttio-mcp",
    args=[],
    env={"TTIO_WB_URL": "https://wb.example.com:18443"},
)

async def run():
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print([t.name for t in tools.tools])   # 28 ttio_* tools
```

### IDE plugins

Most MCP-capable IDE plugins want a **command** and an **env map**. Point
the command at the `ttio-mcp` console script (full path if in a venv) and
set the `TTIO_WB_*` variables — same as the Claude Code wire-up.

---

## First end-to-end test

Once the server is connected to a client and authenticated, exercise it
end to end:

1. Call `ttio_connection_status` — confirm `connected: true` with your
   username and projects. (If headless auto-connect is configured this is
   already true; otherwise call `ttio_login` first.)
2. Call `ttio_whoami` — see your username, projects, and capabilities.
3. Call `ttio_containers_list` (optionally with a `project` filter) — a
   page of containers visible to your account.
4. Pick a container URI from that list and call `ttio_container_manifest`
   with it — the HDF5 manifest projection (runs, counts, ISA ids).
5. Run a cohort query: `ttio_cohort_preview_count` with
   `{"select": "containers", "predicate": {...}}` to get a row count,
   then `ttio_cohort_query` to fetch rows.
6. Download a container to a local file:
   `ttio_download` with `{"container_uri": "...", "out_path": "/tmp/x.tio"}`
   (default `mode: plain`).
7. Inspect the downloaded file with the **Data** tools (these need no
   session): `ttio_dataset_summary` with `{"path": "/tmp/x.tio"}`, then
   `ttio_dataset_read` with `{"path": "/tmp/x.tio", "what": "runs"}`.
8. Export a spectrum's full arrays: `ttio_dataset_export` with
   `{"path": "/tmp/x.tio", "run": "<run-id>", "index": 0, "fmt": "parquet"}`.
   The file lands in `TTIO_MCP_EXPORT_DIR`.

That's the core round trip. Jobs, sessions, and the encrypted transfer
modes (`byok`, `server-kek`, `pqc`) follow the same pattern; see
[docs/tools.md](docs/tools.md) for every tool's parameters.

### Running the live integration suite

The 12 skipped tests become a real conformance check against a running
workbench. From a development clone:

```bash
TTIO_MCP_LIVE=1 \
TTIO_WB_URL="wss://wb.example.com:18443/transport" \
TTIO_WB_TOKEN="ttiowbk_abc123..." \
pytest tests/integration
```

`tests/integration/test_live_smoke.py` covers the read surface plus a
data round-trip; `test_live_full.py` covers the full tool matrix
including the encrypted transfer modes. See the test files for the full
list of accepted credential/fixture environment variables.

---

## Upgrading to a new version

### Installed via pip (Option A)

Re-install pinned to the new tag:

```bash
pip install --upgrade \
  "ttio-mcp @ git+https://github.com/DTW-Thalion/TTIO-MCP-Server.git@vX.Y.Z"
```

### Installed from a clone (Option B)

```bash
cd TTIO-MCP-Server
git fetch --tags
git checkout vX.Y.Z
source .venv/bin/activate
pip install -e ".[dev]"        # re-resolves dependencies, including ttio
pytest -q                      # sanity check
```

Then restart the MCP client so it relaunches `ttio-mcp` with the new
code. In Claude Code that's usually just starting a new session. There
is no database or catalog to migrate — the server is stateless apart from
its in-memory session.

---

## Deployment options

Because the MCP transport is **stdio**, "deploying" this server means
making it easy for each user to install it locally and point it at a
workbench. There is no central daemon to stand up.

### Single user, desktop

The [Quickstart](#quickstart) is the whole story: install, set
`TTIO_WB_URL`, `claude mcp add`, log in interactively. The workbench
account governs what the LLM can see and do.

### Unattended / shared host

- Use an **API key** (`ttiowbk_...`) in `TTIO_WB_TOKEN` so the server
  auto-connects with no interactive login. Issue a dedicated, least-
  privilege workbench account for the MCP server rather than reusing a
  human's credentials.
- Each MCP client launches its own `ttio-mcp` subprocess. The server is
  stateless, so multiple users can each run their own instance against
  the same workbench with their own credentials.
- Keep the API key out of shell history and version control — inject it
  through the client's env config or a secrets manager, not a checked-in
  file.

### LLM on a different machine than the workbench

The MCP transport is local (stdio), but the workbench connection is
ordinary HTTPS/WSS. So:

- Run `ttio-mcp` **on the same machine as the MCP client**, and make
  sure that machine can reach the workbench URL over the network
  (firewall, VPN, TLS as appropriate).
- There is **no** supported MCP-over-HTTP/SSE mode in v0.9.0 — you cannot
  run `ttio-mcp` as a shared remote endpoint that multiple LLMs dial
  into. If you need that, it requires adding a remote transport (a
  roadmap item), not a config change.

### Security notes

- The workbench session token lives only in the server process's memory
  and is never written to disk. Killing the process drops it.
- API keys are bearer credentials: anyone who can read the environment of
  the `ttio-mcp` process can use the key until it is revoked. Scope the
  account and rotate keys accordingly.
- The server exposes no admin or destructive workbench operations, so a
  compromised LLM session is bounded by the workbench account's non-admin
  permissions.

---

## Troubleshooting

### "command not found: ttio-mcp"

The console script isn't on your PATH. If you installed into a venv,
either activate it (`source ~/ttio-mcp-venv/bin/activate`) or use the
full path (`~/ttio-mcp-venv/bin/ttio-mcp`) everywhere — including in
`claude mcp add`.

### The client shows "ttio-mcp disconnected" right after starting

The server crashed during startup. Run it directly from a shell to see
the traceback on stderr:

```bash
ttio-mcp
```

Press Ctrl-D to give it EOF and let it exit cleanly. A common cause is a
malformed `TTIO_MCP_PAGE_SIZE` (must be an integer).

### Tools return "Not connected. Call ttio_login …"

No session is established. Either call `ttio_login` from the LLM, or set
both `TTIO_WB_URL` and `TTIO_WB_TOKEN` **before** the client starts the
server (clients don't forward env vars you export afterward — re-run
`claude mcp add` with the vars set, or put them in the client's config).

### Tools return "Session expired. Call ttio_login again"

Your interactive (password) session passed its ~24h lifetime. Call
`ttio_login` again. API-key (`ttiowbk_...`) sessions do not expire, so if
you see this with an API key, the key was likely revoked server-side —
get a new one from a workbench administrator.

### Headless auto-connect silently doesn't happen

Auto-connect only fires when **both** `TTIO_WB_URL` and `TTIO_WB_TOKEN`
are set in the launching environment, and it fails quietly if the
workbench is unreachable or the token is bad. Call
`ttio_connection_status` to see the state, then check the URL is correct
and reachable (`curl -I "$TTIO_WB_URL"`) and the key is valid.

### Install fails building the `ttio` SDK

The `ttio` dependency is compiled from source. Make sure `git` and a C
toolchain are installed (`build-essential` on Debian/Ubuntu, Xcode
Command Line Tools on macOS), then retry. Watch the pip output for the
real compiler error.

### A PQC (`pqc` mode) transfer corrupts the connection

This was a real bug fixed in v0.9.0: `liboqs` writes a banner to file
descriptor 1 (stdout) on import, which would corrupt the JSON-RPC stream.
The server now reserves stdout exclusively for the protocol and redirects
stray writes to stderr. If you see protocol corruption on PQC transfers,
confirm you're on v0.9.0 or later and that `pqc` is installed
(`pip install "ttio-mcp[pqc] @ git+…"`).

### `ttio_dataset_*` tools fail on a path

The Data tools read a **local** `.tio` file. Make sure the `path` exists
on the machine running `ttio-mcp` (e.g. a file you fetched with
`ttio_download`), not a workbench container URI.

---

## Uninstall

```bash
# 1. Remove from your MCP client (Claude Code example)
claude mcp remove ttio-mcp

# 2a. If installed with pip into the active environment
pip uninstall ttio-mcp

# 2b. If installed into a dedicated venv, just delete it
rm -rf ~/ttio-mcp-venv

# 3. If installed from a clone, delete the repo
rm -rf ~/TTIO-MCP-Server

# 4. Remove any exported state (created lazily; safe to delete)
rm -rf ~/.local/state/ttio-mcp

# 5. If you added TTIO_* lines to a shell profile, remove them
#    Edit ~/.bashrc / ~/.zshrc / your PowerShell profile.
```

The server holds no database, keyring, or catalog, so there is nothing
else to clean up. Revoke any API key you issued for it from the workbench
Operations Dashboard if it is no longer needed.

---

## Where to go next

- [README.md](README.md) — project summary and tool overview.
- [docs/tools.md](docs/tools.md) — reference for every MCP tool.
- [docs/configuration.md](docs/configuration.md) — full env-var reference.
- [CHANGELOG.md](CHANGELOG.md) — what changed in each release.
- [tti-workbench-server](https://github.com/DTW-Thalion/tti-workbench-server)
  — the server this client talks to.
