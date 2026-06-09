"""Lazy spectrum read from disk.

This is the only M3 tool that reopens the underlying ``.mpgo`` file —
all other query tools answer from the catalog. Downsamples arrays
past ``max_points`` so responses stay within MCP message budgets.
"""
from __future__ import annotations

import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ttio_mcp.catalog import CatalogError, NotFound, resolve_uri
from ttio_mcp.db.models import File, Run
from ttio_mcp.keyring import AES_256_GCM, Keyring
from ttio_mcp.tools._fsspec_defaults import merged_fsspec_kwargs

DEFAULT_MAX_POINTS = 1000
MAX_POINTS_CAP = 100_000

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "run_id": {"type": "integer", "minimum": 1},
        "file_id": {"type": "integer", "minimum": 1},
        "run_name": {"type": "string"},
        "spectrum_index": {"type": "integer", "minimum": 0},
        "max_points": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_POINTS_CAP,
            "default": DEFAULT_MAX_POINTS,
        },
        "fsspec_kwargs": {
            "type": "object",
            "description": (
                "Optional fsspec kwargs for remote URIs. Shallow-merged on top "
                "of TTIO_MCP_FSSPEC_KWARGS (per-call keys win). Ignored for "
                "local files."
            ),
            "additionalProperties": True,
        },
        "key_id": {
            "type": "string",
            "description": (
                "Keyring id for decrypting the intensity channel in-memory "
                "(read-only; disk bytes are not touched). Required when the "
                "catalog row's encrypted=true."
            ),
        },
    },
    "oneOf": [
        {"required": ["run_id", "spectrum_index"]},
        {"required": ["file_id", "run_name", "spectrum_index"]},
    ],
}


class ReadFailed(CatalogError):
    code = "read_failed"


class InvalidArgument(CatalogError):
    code = "invalid_argument"


class KeyRequired(CatalogError):
    code = "key_required"


async def handle(
    session: Session,
    args: dict[str, Any],
    *,
    keyring: Keyring,
) -> dict[str, Any]:
    spectrum_index = int(args["spectrum_index"])
    max_points = int(args.get("max_points", DEFAULT_MAX_POINTS))
    fsspec_kwargs = merged_fsspec_kwargs(args.get("fsspec_kwargs"))
    key_id = args.get("key_id")

    if "run_id" in args and args["run_id"] is not None:
        run = session.get(Run, int(args["run_id"]))
        if run is None:
            raise NotFound(f"no run with id={args['run_id']}")
    else:
        stmt = select(Run).where(
            Run.file_id == int(args["file_id"]),
            Run.name == args["run_name"],
        )
        run = session.execute(stmt).scalar_one_or_none()
        if run is None:
            raise NotFound(
                f"no run named {args['run_name']!r} in file_id={args['file_id']}"
            )

    if spectrum_index >= run.spectrum_count:
        raise InvalidArgument(
            f"spectrum_index {spectrum_index} out of range "
            f"(run has {run.spectrum_count} spectra)"
        )

    f = session.get(File, run.file_id)
    if f is None:  # pragma: no cover - FK guarantees
        raise NotFound(f"orphan run id={run.id}")

    try:
        target = resolve_uri(f.uri, fsspec_kwargs=fsspec_kwargs)
    except CatalogError as exc:
        raise ReadFailed(f"cannot read file at {f.uri}: {exc}") from exc

    open_target = target.local_path if not target.is_remote else target.canonical_uri

    from mpeg_o import SpectralDataset

    try:
        dataset = (
            SpectralDataset.open(open_target, **fsspec_kwargs)
            if target.is_remote
            else SpectralDataset.open(open_target)
        )
    except Exception as exc:
        raise ReadFailed(f"{open_target}: {type(exc).__name__}: {exc}") from exc

    try:
        if f.encrypted:
            if not key_id:
                raise KeyRequired(
                    f"file id={f.id} is encrypted "
                    f"(algorithm={f.encrypted_algorithm or 'unknown'}); "
                    f"pass key_id to decrypt at read time"
                )
            key = keyring.get(key_id, expected_algorithm=AES_256_GCM)
            try:
                dataset.decrypt_with_key(key)
            except Exception as exc:
                raise ReadFailed(
                    f"decrypt_with_key failed on {open_target}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
        ttio_run = dataset.all_runs.get(run.name)
        if ttio_run is None:
            raise ReadFailed(f"run {run.name!r} not found in {open_target}")
        spec = ttio_run.object_at_index(spectrum_index)
        payload = _serialize_spectrum(
            spec, run, spectrum_index, max_points=max_points
        )
    finally:
        dataset.close()

    return payload


def _serialize_spectrum(
    spec,  # type: ignore[no-untyped-def]
    run: Run,
    spectrum_index: int,
    *,
    max_points: int,
) -> dict[str, Any]:
    channel_names = (run.metadata_json or {}).get("channel_names") or list(
        getattr(spec, "signal_array_names", ()) or ()
    )

    channels: dict[str, list[float]] = {}
    original_length = 0
    for name in channel_names:
        arr = _channel_array(spec, name)
        if arr is None:
            continue
        if arr.size > original_length:
            original_length = int(arr.size)
        channels[name] = _downsample(arr, max_points).tolist()

    truncated = original_length > max_points
    returned_length = (
        len(next(iter(channels.values()))) if channels else 0
    )

    metadata: dict[str, Any] = {}
    for attr in (
        "ms_level",
        "polarity",
        "precursor_mz",
        "precursor_charge",
        "scan_time_seconds",
        "nucleus_type",
        "spectrometer_frequency_mhz",
    ):
        if hasattr(spec, attr):
            v = getattr(spec, attr)
            if v is None or v == "":
                continue
            metadata[_metadata_key(attr)] = _coerce_scalar(v)

    return {
        "run_id": run.id,
        "run_name": run.name,
        "spectrum_index": spectrum_index,
        "spectrum_class": type(spec).__name__,
        "channels": channels,
        "metadata": metadata,
        "truncated": truncated,
        "original_length": original_length,
        "returned_length": returned_length,
    }


def _channel_array(spec, name: str):  # type: ignore[no-untyped-def]
    """Pull the underlying numpy array for a named channel.

    TTI-O returns a :class:`SignalArray` wrapper (``.data`` is the
    ndarray). For named convenience accessors (``spec.mz_array``)
    prefer those; otherwise fall through to ``spec.signal_array(name)``.
    """
    direct = f"{name}_array"
    obj = None
    if hasattr(spec, direct):
        obj = getattr(spec, direct)
    elif hasattr(spec, "signal_array"):
        try:
            obj = spec.signal_array(name)
        except Exception:
            return None
    if obj is None:
        return None
    # SignalArray wrapper vs raw ndarray.
    return getattr(obj, "data", obj)


def _downsample(arr, max_points: int):  # type: ignore[no-untyped-def]
    if arr.size <= max_points:
        return arr
    stride = math.ceil(arr.size / max_points)
    return arr[::stride]


def _metadata_key(attr: str) -> str:
    # MS exposes scan_time_seconds; callers expect "retention_time" to
    # mirror the per-run index. Normalise the label here.
    if attr == "scan_time_seconds":
        return "retention_time"
    return attr


def _coerce_scalar(v):  # type: ignore[no-untyped-def]
    try:
        return float(v)
    except (TypeError, ValueError):
        return v
