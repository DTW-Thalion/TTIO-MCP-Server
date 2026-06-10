# src/ttio_mcp/summarize.py
"""Token-cheap summaries of numeric arrays for inline tool results."""
from __future__ import annotations

from typing import Any

import numpy as np


def array_summary(a: np.ndarray) -> dict[str, Any]:
    """Compact stats for a 1-D numeric array."""
    a = np.asarray(a)
    if a.size == 0:
        return {"count": 0}
    return {
        "count": int(a.size),
        "min": float(np.min(a)),
        "max": float(np.max(a)),
        "mean": float(np.mean(a)),
        "sum": float(np.sum(a)),
    }


def top_peaks(x: np.ndarray, y: np.ndarray, n: int = 10) -> list[dict[str, float]]:
    """Return the n highest-y points as paired (mz, intensity) dicts, descending by intensity."""
    x = np.asarray(x)
    y = np.asarray(y)
    if x.size == 0:
        return []
    idx = np.argsort(y)[::-1][:n]
    return [{"mz": float(x[i]), "intensity": float(y[i])} for i in idx]


def downsample(a: np.ndarray, max_points: int = 200) -> list[float]:
    """Uniformly subsample a 1-D array to at most max_points for a preview."""
    a = np.asarray(a)
    if a.size <= max_points:
        return [float(v) for v in a]
    step = int(np.ceil(a.size / max_points))
    return [float(v) for v in a[::step]]
