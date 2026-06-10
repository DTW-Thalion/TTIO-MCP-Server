# tests/test_summarize.py
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
