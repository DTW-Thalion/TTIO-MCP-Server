# tests/test_tools_data.py
import asyncio
import contextlib

import numpy as np
from mcp.server.fastmcp import FastMCP

from ttio_mcp.config import Config
from ttio_mcp.connection import ConnectionManager
from ttio_mcp.tools import data as dt


class _Sig:
    def __init__(self, arr):
        self.data = np.asarray(arr)


class _Spectrum:
    def __init__(self):
        self.mz_array = _Sig([100.0, 200.0, 300.0])
        self.intensity_array = _Sig([5.0, 50.0, 25.0])

    def signal_array(self, name):
        return {"mz": self.mz_array, "intensity": self.intensity_array}[name]

    def signal_array_names(self):
        return ["mz", "intensity"]


class _Run:
    def __len__(self): return 2
    def __getitem__(self, i): return _Spectrum()


class _DS:
    title = "demo"
    is_encrypted = False

    @property
    def runs(self): return {"run_0001": _Run()}
    ms_runs = {"run_0001": _Run()}

    @property
    def subjects(self): return [{"external_id": "S1"}]

    @property
    def samples(self): return [{"sample_kind": "plasma"}]

    def identifications(self): return []
    def quantifications(self): return []
    def provenance(self): return []


def _patch_open(monkeypatch):
    @contextlib.contextmanager
    def fake_open(path, **kw):
        yield _DS()
    monkeypatch.setattr(dt.SpectralDataset, "open", staticmethod(fake_open))


def _app(monkeypatch):
    _patch_open(monkeypatch)
    cm = ConnectionManager()
    app = FastMCP("t")
    dt.register(app, cm, Config.from_env())
    return app


def _call(app, name, **kw):
    res = app._tool_manager.get_tool(name).fn(**kw)
    return asyncio.run(res) if asyncio.iscoroutine(res) else res


def test_dataset_summary(monkeypatch, tmp_path):
    app = _app(monkeypatch)
    out = _call(app, "ttio_dataset_summary", path=str(tmp_path / "x.tio"))
    assert out["title"] == "demo"
    assert out["runs"]["run_0001"]["spectra"] == 2


def test_dataset_read_spectrum(monkeypatch, tmp_path):
    app = _app(monkeypatch)
    out = _call(app, "ttio_dataset_read", path=str(tmp_path / "x.tio"),
                what="spectrum", run="run_0001", index=0)
    assert out["top_peaks"][0]["mz"] == 200.0
    assert out["mz"]["count"] == 3


def test_dataset_read_subjects(monkeypatch, tmp_path):
    app = _app(monkeypatch)
    out = _call(app, "ttio_dataset_read", path=str(tmp_path / "x.tio"), what="subjects")
    assert out["subjects"][0]["external_id"] == "S1"


def test_dataset_export_spectrum(monkeypatch, tmp_path):
    app = _app(monkeypatch)
    out = _call(app, "ttio_dataset_export", path=str(tmp_path / "x.tio"),
                run="run_0001", index=0, out_dir=str(tmp_path), fmt="json")
    assert out["export_path"].endswith(".json")
