# MPEG-O MCP Server

An [MCP](https://modelcontextprotocol.io/) server that exposes
[MPEG-O](https://github.com/DTW-Thalion/MPEG-O) (`.mpgo`) file
capabilities to LLM clients via a catalog pattern — a SQL index over
files that stay in place on disk or in object storage.

> **M1 scaffolding only.** The server currently answers the MCP
> `initialize` handshake and nothing else. Tool handlers land in M2/M3.

## Status

| Milestone | Status                             |
|-----------|------------------------------------|
| M1: Foundation & Schema | **in progress** (this commit)      |
| M2: Catalog & file registration | planned                          |
| M3: Querying & identifications | planned                          |
| M4: Secrets, cloud, encryption | planned                          |
| M5: Conformance + TestPyPI publish | planned                          |

## Requirements

- Python 3.11 or 3.12
- git (runtime install pulls the `mpeg-o` dependency from the MPEG-O
  v1.0.0 git tag; PyPI publish is tracked as MPEG-O M40)

## Install

```bash
git clone https://github.com/DTW-Thalion/MPEG-O-MCP-Server.git
cd MPEG-O-MCP-Server
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Bootstrap the catalog

```bash
export MPGO_MCP_DB_URL="sqlite:///mpeg_o_mcp.db"   # default if unset
alembic upgrade head
```

Creates the six tables and seeds a `system` user (used by the
forward-compat `registered_by` / `owner_user_id` columns on `files`).
`alembic downgrade base` reverses the migration.

## Run

```bash
mpeg-o-mcp
```

The server speaks MCP over stdio. With no tools registered in M1, the
only useful exchange is the `initialize` handshake — e.g.:

```bash
claude mcp add mpeg-o-mcp -- mpeg-o-mcp
```

The full tool list lands in M2.

## Development

```bash
pytest -q
ruff check .
alembic upgrade head && alembic downgrade base   # round-trip
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
