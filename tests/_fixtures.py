"""Helpers for building small ``.mpgo`` fixtures in-test.

No vendored binaries — every fixture is built from scratch via
:func:`mpeg_o.SpectralDataset.write_minimal` so the tests stay
hermetic and the fixture shape tracks MPEG-O's API.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from mpeg_o import (
    AcquisitionMode,
    Identification,
    ProvenanceRecord,
    Quantification,
    SpectralDataset,
    WrittenRun,
)


def build_ms_fixture(
    path: Path,
    *,
    title: str = "demo-ms",
    isa: str = "ISA-MS-1",
    n_spectra: int = 5,
    n_points: int = 8,
) -> Path:
    rng = np.random.default_rng(42)
    mz = np.tile(np.linspace(100.0, 200.0, n_points), n_spectra).astype(np.float64)
    intensity = rng.uniform(0.0, 1e6, size=n_spectra * n_points).astype(np.float64)
    run = WrittenRun(
        spectrum_class="MPGOMassSpectrum",
        acquisition_mode=int(AcquisitionMode.MS1_DDA),
        channel_data={"mz": mz, "intensity": intensity},
        offsets=(np.arange(n_spectra, dtype=np.uint64) * n_points),
        lengths=np.full(n_spectra, n_points, dtype=np.uint32),
        retention_times=np.linspace(0.0, 4.0, n_spectra),
        ms_levels=np.ones(n_spectra, dtype=np.int32),
        polarities=np.ones(n_spectra, dtype=np.int32),
        precursor_mzs=np.zeros(n_spectra),
        precursor_charges=np.zeros(n_spectra, dtype=np.int32),
        base_peak_intensities=intensity.reshape(n_spectra, n_points).max(axis=1),
    )
    SpectralDataset.write_minimal(
        path,
        title=title,
        isa_investigation_id=isa,
        runs={"run_0001": run},
        identifications=[
            Identification("run_0001", 0, "CHEBI:15377", 0.95, ["ev:peak"]),
            Identification("run_0001", 2, "CHEBI:28001", 0.70, ["ev:ms2"]),
        ],
        quantifications=[
            Quantification("CHEBI:15377", "run_0001", 1234.5, "median"),
            Quantification("CHEBI:28001", "run_0001", 56.78, "median"),
        ],
        provenance=[
            ProvenanceRecord(
                timestamp_unix=1_700_000_000,
                software="demo-writer 1.0",
                parameters={"note": "synthetic"},
                input_refs=["urn:raw:sample-a"],
                output_refs=[str(path)],
            )
        ],
    )
    return path


def build_nmr_fixture(path: Path, *, title: str = "demo-nmr") -> Path:
    n_pts = 32
    cs = np.linspace(-1.0, 12.0, n_pts).astype(np.float64)
    intensity = np.linspace(0.0, 1.0, n_pts).astype(np.float64)
    run = WrittenRun(
        spectrum_class="MPGONMRSpectrum",
        acquisition_mode=int(AcquisitionMode.NMR_1D),
        channel_data={"chemical_shift": cs, "intensity": intensity},
        offsets=np.array([0], dtype=np.uint64),
        lengths=np.array([n_pts], dtype=np.uint32),
        retention_times=np.zeros(1),
        ms_levels=np.zeros(1, dtype=np.int32),
        polarities=np.zeros(1, dtype=np.int32),
        precursor_mzs=np.zeros(1),
        precursor_charges=np.zeros(1, dtype=np.int32),
        base_peak_intensities=np.zeros(1),
        nucleus_type="1H",
    )
    SpectralDataset.write_minimal(
        path,
        title=title,
        isa_investigation_id="ISA-NMR-1",
        runs={"nmr_run": run},
    )
    return path
