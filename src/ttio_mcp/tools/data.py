# src/ttio_mcp/tools/data.py
"""Local .tio reading/extraction tools: summaries inline, full arrays via export."""
from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from ttio import SpectralDataset

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.errors import ToolError, to_tool_error
from ttio_mcp.export import export_arrays
from ttio_mcp.summarize import array_summary, downsample, top_peaks

_READABLE = {"runs", "spectrum", "signal", "subjects", "samples",
             "images", "identifications", "quantifications", "provenance"}


def _obj(o: Any) -> Any:
    """Best-effort plain-dict view of a record (dict, dataclass, or object)."""
    if isinstance(o, dict):
        return o
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    return {k: getattr(o, k) for k in dir(o)
            if not k.startswith("_") and not callable(getattr(o, k))}


def _spectrum(ds: Any, run: str, index: int):
    runs = ds.runs
    if run not in runs:
        raise ToolError(f"run {run!r} not found; available: {sorted(runs)}")
    r = runs[run]
    if index < 0 or index >= len(r):
        raise ToolError(f"index {index} out of range (run has {len(r)} spectra)")
    return r[index]


def register(app: FastMCP, conn: ConnectionManager, config: Config) -> None:
    async def _run(fn, *a, **k):
        return await asyncio.to_thread(fn, *a, **k)

    @app.tool()
    async def ttio_dataset_summary(path: str) -> dict:
        """Summarize a local .tio: title, encryption, runs (with spectrum counts), subject/sample counts."""
        def work():
            with SpectralDataset.open(path) as ds:
                runs = {name: {"spectra": len(r)} for name, r in ds.runs.items()}
                return {
                    "title": getattr(ds, "title", None),
                    "is_encrypted": bool(getattr(ds, "is_encrypted", False)),
                    "runs": runs,
                    "subject_count": len(ds.subjects),
                    "sample_count": len(ds.samples),
                }
        try:
            return await _run(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}

    @app.tool()
    async def ttio_dataset_read(path: str, what: str, run: str | None = None, index: int = 0,
                                signal: str | None = None, max_points: int = 200,
                                top_n: int = 10, limit: int = 100) -> dict:
        """Read part of a local .tio. what=runs|spectrum|signal|subjects|samples|images|
        identifications|quantifications|provenance. Returns compact summaries; use
        ttio_dataset_export for full arrays."""
        if what not in _READABLE:
            return {"error": f"what must be one of {sorted(_READABLE)}"}

        def work():
            with SpectralDataset.open(path) as ds:
                if what == "runs":
                    return {"runs": {n: {"spectra": len(r)} for n, r in ds.runs.items()}}
                if what == "spectrum":
                    if run is None:
                        raise ToolError("what=spectrum requires run=")
                    sp = _spectrum(ds, run, index)
                    mz = sp.mz_array.data
                    inten = sp.intensity_array.data
                    return {
                        "run": run, "index": index,
                        "mz": array_summary(mz),
                        "intensity": array_summary(inten),
                        "top_peaks": top_peaks(mz, inten, n=top_n),
                        "mz_preview": downsample(mz, max_points),
                    }
                if what == "signal":
                    if run is None or signal is None:
                        raise ToolError("what=signal requires run= and signal=")
                    sp = _spectrum(ds, run, index)
                    arr = sp.signal_array(signal).data
                    return {"run": run, "index": index, "signal": signal,
                            "summary": array_summary(arr), "preview": downsample(arr, max_points)}
                if what == "subjects":
                    return {"subjects": [_obj(s) for s in ds.subjects[:limit]]}
                if what == "samples":
                    return {"samples": [_obj(s) for s in ds.samples[:limit]]}
                if what == "images":
                    return {"images": sorted(str(k) for k in getattr(ds, "images", {}).keys())}
                if what == "identifications":
                    return {"identifications": [_obj(x) for x in ds.identifications()[:limit]]}
                if what == "quantifications":
                    return {"quantifications": [_obj(x) for x in ds.quantifications()[:limit]]}
                return {"provenance": [_obj(x) for x in ds.provenance()[:limit]]}
        try:
            return await _run(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}

    @app.tool()
    async def ttio_dataset_export(path: str, run: str, index: int = 0,
                                  out_dir: str | None = None, basename: str | None = None,
                                  fmt: str = "parquet") -> dict:
        """Export a spectrum's full arrays (all signal channels) to a file. fmt=parquet|csv|json."""
        target = Path(out_dir) if out_dir else config.export_dir

        def work():
            with SpectralDataset.open(path) as ds:
                sp = _spectrum(ds, run, index)
                names = sp.signal_array_names() if hasattr(sp, "signal_array_names") else ["mz", "intensity"]
                arrays = {n: sp.signal_array(n).data for n in names}
                bn = basename or f"{run}_{index}"
                return export_arrays(arrays, out_dir=target, basename=bn, fmt=fmt)
        try:
            p = await _run(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": to_tool_error(exc)}
        return {"export_path": p}
