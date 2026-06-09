# HANDOFF-M3.md — TTI-O-MCP M3: Querying, Spectra & Quantifications

## Context

M2 shipped the catalog: register a `.mpgo`, extract its metadata,
list / get / reverify it through four MCP tools. M3 makes the
catalog *useful to an LLM client* — cross-file identification search,
per-run detail, **lazy spectrum reads**, and first-class
quantifications.

- M1 HANDOFF: [HANDOFF.md](HANDOFF.md) — binding decisions (auth,
  extraction policy, tool granularity).
- M2 HANDOFF: [HANDOFF-M2.md](HANDOFF-M2.md) — catalog surface, error
  model, workflow conventions. M3 inherits all of it.

## M3 Scope

- **New table `quantifications`**. Eagerly materialized at
  registration time from `SpectralDataset.quantifications()` — same
  reasoning as identifications: clients ask "what abundance values
  exist for CHEBI:X?" far more often than "open this file".
- **Extractor update** (`catalog._extract` / `register_file`). Add
  quantifications to the extraction payload. Re-registration clears
  and replaces quantifications atomically alongside the other child
  tables.
- **Four MCP query tools**:
  1. `ttio_search_identifications(chebi_id?, name_contains?, min_score?, acquisition_mode?, file_id?, limit?, offset?)`
     — cross-file, paginated. Joins `identifications → runs → files`
     so results include the file URI + run name without a second
     round-trip.
  2. `ttio_get_run(run_id | (file_id, run_name))` — per-run detail:
     run record + its identifications + quantifications (filtered to
     rows whose `sample_ref` matches the run name, if any; otherwise
     file-level).
  3. `ttio_get_spectrum(run_id | (file_id, run_name), spectrum_index, max_points?)`
     — **the only tool that reads from disk outside of registration.**
     Re-opens the `.mpgo` via the stored URI, reads exactly one
     spectrum, returns channels + per-spectrum metadata. Truncates
     arrays past `max_points` (default 1000, cap 100000) via stride
     downsampling.
  4. `ttio_get_quantifications(file_id | uri, chebi_id?, sample_ref?, min_abundance?, limit?, offset?)`
     — paginated, per-file.

- **Local URIs only** still. No cloud, no encryption — M4.
- **Sync I/O stays under `asyncio.to_thread`**. Same pattern as M2.
- **Fixtures**: update `build_ms_fixture` to include a quantification
  entry (matches the existing identifications). New fixtures only if
  a test genuinely needs a distinct shape.

## Out of Scope for M3

- Remote URIs (s3://, https://), fsspec, keyring — M4.
- Encryption / signed bundles — M4.
- Features, transitions, chromatograms — deferred (no query tools
  need them yet; we can add catalog rows in a follow-up if client
  demand shows up).
- Streaming / chunked responses over MCP — M4+.
- Full-text indexing over names (SQLite `LIKE %x%` is fine at M3
  scale).
- TestPyPI publish — M5.

## Package Layout (new/changed in M3)

```
src/ttio_mcp/
├── db/
│   └── models.py              # UPDATED — Quantification model + File.quantifications rel
├── catalog.py                 # UPDATED — _extract + register_file populate quantifications
└── tools/
    ├── __init__.py            # UPDATED — register 4 new tool handlers
    ├── search_identifications.py  # NEW
    ├── get_run.py                 # NEW
    ├── get_spectrum.py            # NEW
    └── get_quantifications.py     # NEW

migrations/versions/
└── <new>_quantifications.py   # NEW — on top of d556e849b813

tests/
├── _fixtures.py               # UPDATED — MS fixture gets a quantification
├── test_catalog.py            # UPDATED — assert quantifications round-trip
└── test_m3_tools.py           # NEW — one test per new tool
```

## Schema — `quantifications`

```python
class Quantification(Base):
    __tablename__ = "quantifications"

    id                   = Integer PK, autoincrement
    file_id              = Integer FK files.id ON DELETE CASCADE, NOT NULL
    chebi_id             = String, indexed, nullable
    name                 = String, nullable             # same value as chebi_id in v0.3
    sample_ref           = String, indexed, nullable
    abundance            = Float,  indexed, nullable
    normalization_method = String, nullable
    metadata_json        = JSON, default={}
```

Composite index `ix_quantifications_chebi_sample` on
`(chebi_id, sample_ref)` — the hottest query shape ("what's the
abundance of X in sample Y").

## Tool Contracts (rough)

All tools share the M2 envelope: `{"ok": true, "data": ...}` or
`{"ok": false, "error": {"code", "message"}}`. Errors use the M2 code
set, plus:

- `invalid_argument` — a required oneOf branch isn't satisfied, or
  `spectrum_index` is out of range, or `max_points` exceeds cap.
- `read_failed` — the file re-opened for a spectrum read no longer
  exists / can't be parsed. (Distinct from `resolve_failed` because
  the file was in the catalog at some point.)

### `ttio_search_identifications`

```json
{
  "type": "object",
  "properties": {
    "chebi_id":         {"type": "string"},
    "name_contains":    {"type": "string"},
    "min_score":        {"type": "number", "minimum": 0, "maximum": 1},
    "acquisition_mode": {"type": "string", "description": "e.g. MS1_DDA"},
    "file_id":          {"type": "integer"},
    "limit":            {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
    "offset":           {"type": "integer", "minimum": 0, "default": 0}
  },
  "additionalProperties": false
}
```

Response `data`:
```json
{
  "total": 42,
  "limit": 50,
  "offset": 0,
  "identifications": [
    {
      "id": 17,
      "file_id": 3,
      "file_uri": "file:///.../demo.mpgo",
      "run_id": 7,
      "run_name": "run_0001",
      "acquisition_mode": "MS1_DDA",
      "chebi_id": "CHEBI:15377",
      "name": "CHEBI:15377",
      "score": 0.95,
      "spectrum_index": 0,
      "evidence_chain": ["ev:peak"]
    }
  ]
}
```

Sort: `score DESC, id ASC` (stable, reproducible pagination).

### `ttio_get_run`

```json
{
  "type": "object",
  "properties": {
    "run_id":   {"type": "integer"},
    "file_id":  {"type": "integer"},
    "run_name": {"type": "string"}
  },
  "oneOf": [
    {"required": ["run_id"]},
    {"required": ["file_id", "run_name"]}
  ]
}
```

Response `data`:
```json
{
  "id": 7,
  "file_id": 3,
  "name": "run_0001",
  "acquisition_mode": "MS1_DDA",
  "spectrum_count": 5,
  "instrument_manufacturer": null,
  "instrument_model": null,
  "polarity": "positive",
  "nucleus_type": null,
  "channel_names": ["mz", "intensity"],
  "identifications": [ ... same shape as search above, no file_uri repeated ... ],
  "quantifications": [ ... same shape as get_quantifications, scoped to run.name ... ]
}
```

`quantifications` in the run payload = rows where `sample_ref ==
run.name` OR `sample_ref IS NULL` (file-level). This is a convenience
projection; full per-file listing stays on `ttio_get_quantifications`.

### `ttio_get_spectrum`

```json
{
  "type": "object",
  "properties": {
    "run_id":         {"type": "integer"},
    "file_id":        {"type": "integer"},
    "run_name":       {"type": "string"},
    "spectrum_index": {"type": "integer", "minimum": 0},
    "max_points":     {"type": "integer", "minimum": 1, "maximum": 100000, "default": 1000}
  },
  "oneOf": [
    {"required": ["run_id", "spectrum_index"]},
    {"required": ["file_id", "run_name", "spectrum_index"]}
  ]
}
```

Implementation: look up the run → file row → open
`SpectralDataset.open(path)` → `ds.all_runs[run.name].object_at_index(i)`
→ package channels + metadata. Close the dataset in a `finally`.

Channel handling:
- MS: pull `mz_array` and `intensity_array`, expose as
  `{"mz": [...], "intensity": [...]}`.
- NMR: pull `chemical_shift_array` + `intensity_array`, expose as
  `{"chemical_shift": [...], "intensity": [...]}`.
- Generic fallback: iterate `signal_array_names` via `signal_array()`
  so unusual spectrum classes don't blow up the tool.

Downsampling: if `len(array) > max_points`, take `stride =
ceil(len / max_points)` and return `array[::stride]`. Flag
`truncated: true` and include `original_length` in the response.

Response `data`:
```json
{
  "run_id": 7,
  "run_name": "run_0001",
  "spectrum_index": 0,
  "spectrum_class": "MPGOMassSpectrum",
  "channels": {"mz": [100.0, 114.3, ...], "intensity": [0.0, 12.0, ...]},
  "metadata": {
    "retention_time": 0.0, "ms_level": 1, "polarity": 1,
    "precursor_mz": 0.0, "precursor_charge": 0
  },
  "truncated": false,
  "original_length": 8,
  "returned_length": 8
}
```

### `ttio_get_quantifications`

```json
{
  "type": "object",
  "properties": {
    "file_id":       {"type": "integer"},
    "uri":           {"type": "string"},
    "chebi_id":      {"type": "string"},
    "sample_ref":    {"type": "string"},
    "min_abundance": {"type": "number"},
    "limit":         {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
    "offset":        {"type": "integer", "minimum": 0, "default": 0}
  },
  "oneOf": [
    {"required": ["file_id"]},
    {"required": ["uri"]}
  ]
}
```

## Acceptance Checklist

- [ ] Alembic round-trip: `alembic upgrade head && alembic downgrade base`
      clean on both the M2 schema and the new M3 migration.
- [ ] `pytest -q` green — existing 22 M2 tests still pass, plus at
      least one test per new tool, plus a catalog-level test that
      quantifications round-trip and are replaced on re-register.
- [ ] `ruff check .` clean.
- [ ] Server still launches: `python -m ttio_mcp.server` handshakes
      and lists 8 tools (4 M2 + 4 M3).
- [ ] `ttio_get_spectrum` correctly downsamples a run whose spectrum
      length exceeds `max_points` (test builds a fixture with
      `n_points > max_points`).
- [ ] CHANGELOG entry under `[0.3.0.dev0]`.
- [ ] Version bump in `pyproject.toml` and `src/ttio_mcp/__init__.py`
      to `0.3.0.dev0`.

## Workflow

- Direct commits to `main`, `[M3] ...` prefix, one logical change each.
- Build + commit in WSL.
- Push via Windows git:
  `/c/Program\ Files/Git/bin/git.exe -C //wsl.localhost/Ubuntu/home/toddw/TTIO-MCP-Server push origin main`.
- No branches, no PRs — same as M1 / M2.
