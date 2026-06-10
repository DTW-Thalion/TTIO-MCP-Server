# tests/test_export.py
from pathlib import Path

import numpy as np
import pytest

from ttio_mcp.export import export_arrays


def test_export_parquet(tmp_path):
    p = export_arrays({"mz": np.array([1.0, 2.0]), "intensity": np.array([9.0, 8.0])},
                      out_dir=tmp_path, basename="spec1", fmt="parquet")
    assert Path(p).exists()
    assert p.endswith(".parquet")


def test_export_csv(tmp_path):
    p = export_arrays({"mz": np.array([1.0, 2.0]), "intensity": np.array([9.0, 8.0])},
                      out_dir=tmp_path, basename="spec1", fmt="csv")
    text = Path(p).read_text()
    assert "mz" in text and "intensity" in text


def test_export_json(tmp_path):
    p = export_arrays({"mz": np.array([1.0, 2.0])}, out_dir=tmp_path, basename="s", fmt="json")
    assert Path(p).read_text().strip().startswith("{")


def test_unequal_lengths_rejected_for_tabular(tmp_path):
    with pytest.raises(ValueError):
        export_arrays({"a": np.array([1.0]), "b": np.array([1.0, 2.0])},
                      out_dir=tmp_path, basename="s", fmt="parquet")
