# tests/test_summarize.py
import json

import numpy as np

from ttio_mcp.summarize import array_summary, downsample, top_peaks


def test_array_summary():
    s = array_summary(np.array([1.0, 2.0, 3.0, 4.0]))
    assert s["count"] == 4
    assert s["min"] == 1.0
    assert s["max"] == 4.0
    assert abs(s["mean"] - 2.5) < 1e-9


def test_top_peaks():
    mz = np.array([100.0, 200.0, 300.0])
    inten = np.array([5.0, 50.0, 25.0])
    peaks = top_peaks(mz, inten, n=2)
    assert peaks[0] == {"mz": 200.0, "intensity": 50.0}
    assert peaks[1]["mz"] == 300.0


def test_downsample_caps_length():
    x = np.arange(1000.0)
    ds = downsample(x, max_points=100)
    assert len(ds) <= 100


def test_non_finite_becomes_none():
    a = np.array([1.0, np.nan, np.inf])
    s = array_summary(a)
    assert s["max"] is None  # inf -> None
    # whole result must be valid JSON (no NaN/Infinity literals)
    json.dumps(s, allow_nan=False)
    peaks = top_peaks(np.array([1.0, 2.0]), np.array([np.nan, 3.0]), n=2)
    json.dumps(peaks, allow_nan=False)
    ds = downsample(np.array([1.0, np.nan]), max_points=10)
    assert ds[1] is None
    json.dumps(ds, allow_nan=False)
