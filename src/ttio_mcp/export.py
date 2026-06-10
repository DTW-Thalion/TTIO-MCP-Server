# src/ttio_mcp/export.py
"""Full-fidelity array export to a local file; returns the path."""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import numpy as np

_FORMATS = {"parquet", "csv", "json"}


def export_arrays(
    arrays: Mapping[str, np.ndarray],
    *,
    out_dir: Path,
    basename: str,
    fmt: str = "parquet",
) -> str:
    """Write named 1-D arrays to out_dir/basename.<fmt>; return the path.

    parquet/csv require equal-length columns; json allows ragged arrays.
    """
    if fmt not in _FORMATS:
        raise ValueError(f"fmt must be one of {sorted(_FORMATS)}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = {k: np.asarray(v) for k, v in arrays.items()}

    if fmt == "json":
        path = out_dir / f"{basename}.json"
        path.write_text(json.dumps({k: v.tolist() for k, v in cols.items()}))
        return str(path)

    lengths = {len(v) for v in cols.values()}
    if len(lengths) > 1:
        raise ValueError(
            f"tabular export requires equal-length columns; got {lengths}"
        )

    if fmt == "csv":
        path = out_dir / f"{basename}.csv"
        names = list(cols)
        rows = zip(*[cols[n] for n in names], strict=True)
        with open(path, "w") as fh:
            fh.write(",".join(names) + "\n")
            for row in rows:
                fh.write(",".join(repr(float(x)) for x in row) + "\n")
        return str(path)

    # parquet
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = out_dir / f"{basename}.parquet"
    table = pa.table({k: v for k, v in cols.items()})
    pq.write_table(table, path)
    return str(path)
