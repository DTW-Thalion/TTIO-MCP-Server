"""File registration and catalog extraction.

``register_file`` is the core atomic operation: resolve the URI, hash
the bytes, open the file through :class:`mpeg_o.SpectralDataset`,
harvest metadata, and write everything into the catalog tables in one
transaction. Idempotent on ``uri`` (re-registering replaces child rows).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpeg_o_mcp.db.models import (
    File,
    Identification,
    ProvenanceRecord,
    Run,
    Study,
    User,
)
from mpeg_o_mcp.hashes import hash_content_sha256, hash_file_sha256

SUPPORTED_SCHEMES = {"file", ""}


class CatalogError(Exception):
    """Base class for catalog errors that should surface as tool errors."""

    code = "internal"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class InvalidURI(CatalogError):
    code = "invalid_uri"


class ResolveFailed(CatalogError):
    code = "resolve_failed"


class NotMpeg(CatalogError):
    code = "not_mpgo"


class NotFound(CatalogError):
    code = "not_found"


@dataclass
class RegistrationResult:
    file_id: int
    uri: str
    file_sha256: str
    format_version: str
    features: list[str]
    counts: dict[str, int]
    was_update: bool


def resolve_local_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme not in SUPPORTED_SCHEMES:
        raise InvalidURI(
            f"scheme {parsed.scheme!r} not supported in M2; only file:// and bare paths"
        )
    raw = parsed.path if parsed.scheme == "file" else uri
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise ResolveFailed(f"{path} does not exist")
    if not path.is_file():
        raise ResolveFailed(f"{path} is not a regular file")
    return path


def canonical_uri(path: Path) -> str:
    return f"file://{path.as_posix()}"


def _resolve_as_user(session: Session, as_user: str | None) -> int:
    name = as_user or "system"
    row = session.execute(select(User).where(User.name == name)).scalar_one_or_none()
    if row is None:
        # M2 behaviour: auto-create unknown user. Real auth lands in M4.
        row = User(name=name)
        session.add(row)
        session.flush()
    return row.id


def _extract(dataset) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    from mpeg_o import FeatureFlags  # noqa: F401  (type shown in docstring)

    ff = dataset.feature_flags
    features = list(ff.features)
    format_version = ff.version

    studies_payload = [
        {
            "title": dataset.title or None,
            "isa_investigation_id": dataset.isa_investigation_id or None,
            "metadata_json": {},
        }
    ]

    runs_payload: list[dict[str, Any]] = []
    for name, run in dataset.all_runs.items():
        ic = run.instrument_config
        runs_payload.append(
            {
                "name": name,
                "acquisition_mode": run.acquisition_mode.name,
                "spectrum_count": int(len(run.index.offsets)),
                "instrument_manufacturer": ic.manufacturer or None,
                "instrument_model": ic.model or None,
                "polarity": _run_polarity(run),
                "metadata_json": {
                    "spectrum_class": run.spectrum_class,
                    "nucleus_type": run.nucleus_type or None,
                    "channel_names": list(run.channel_names),
                },
            }
        )

    # Identifications carry run_name; resolve to run_id after insert.
    id_payload = [
        {
            "run_name": ident.run_name,
            "chebi_id": ident.chemical_entity or None,
            "name": ident.chemical_entity or None,
            "score": float(ident.confidence_score),
            "spectrum_index": int(ident.spectrum_index),
            "metadata_json": {"evidence_chain": list(ident.evidence_chain)},
        }
        for ident in dataset.identifications()
    ]

    prov_payload = [
        {
            "software": rec.software,
            "timestamp": _unix_to_dt(rec.timestamp_unix),
            "input_refs": list(rec.input_refs),
            "output_refs": list(rec.output_refs),
            "metadata_json": {"parameters": dict(rec.parameters)},
        }
        for rec in dataset.provenance()
    ]

    return {
        "format_version": format_version,
        "features": features,
        "studies": studies_payload,
        "runs": runs_payload,
        "identifications": id_payload,
        "provenance": prov_payload,
    }


def _run_polarity(run) -> str | None:  # type: ignore[no-untyped-def]
    import numpy as np

    arr = np.asarray(run.index.polarities)
    if arr.size == 0:
        return None
    vals = set(int(v) for v in arr.tolist())
    # 1 = positive, -1 = negative, 0 = unspecified per MPEG-O convention.
    if vals == {1}:
        return "positive"
    if vals == {-1}:
        return "negative"
    if vals == {0}:
        return None
    return "mixed"


def _unix_to_dt(ts: int) -> datetime | None:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=UTC)


def register_file(
    session: Session,
    uri: str,
    *,
    display_name: str | None = None,
    as_user: str | None = None,
) -> RegistrationResult:
    """Resolve, hash, open, extract, and upsert the file row + children.

    Raises :class:`CatalogError` subclasses on user-visible failures.
    """
    from mpeg_o import SpectralDataset  # lazy import so hashes-only callers don't need it

    path = resolve_local_path(uri)
    canon = canonical_uri(path)
    file_sha = hash_file_sha256(path)
    content_sha = hash_content_sha256(path)

    try:
        dataset = SpectralDataset.open(path)
    except Exception as exc:  # pragma: no cover - MPEG-O raises a variety
        raise NotMpeg(f"{path}: {type(exc).__name__}: {exc}") from exc

    try:
        meta = _extract(dataset)
    finally:
        dataset.close()

    user_id = _resolve_as_user(session, as_user)

    existing = session.execute(select(File).where(File.uri == canon)).scalar_one_or_none()
    was_update = existing is not None

    if existing is not None:
        # Replace children atomically; the ORM cascade handles this on a
        # child clear + flush before we repopulate.
        for coll in (
            existing.studies,
            existing.runs,
            existing.identifications,
            existing.provenance_records,
        ):
            coll.clear()
        session.flush()
        file_row = existing
        file_row.display_name = display_name or file_row.display_name
        file_row.file_sha256 = file_sha
        file_row.content_sha256 = content_sha
        file_row.format_version = meta["format_version"]
        file_row.features = {"list": meta["features"]}
        file_row.last_verified_at = datetime.now(UTC)
    else:
        file_row = File(
            uri=canon,
            display_name=display_name,
            file_sha256=file_sha,
            content_sha256=content_sha,
            format_version=meta["format_version"],
            features={"list": meta["features"]},
            encrypted=False,
            signed=False,
            registered_by=user_id,
            owner_user_id=user_id,
        )
        session.add(file_row)
        session.flush()

    for s in meta["studies"]:
        file_row.studies.append(Study(**s))

    run_name_to_obj: dict[str, Run] = {}
    for r in meta["runs"]:
        r_name = r["name"]
        run_obj = Run(**r)
        file_row.runs.append(run_obj)
        run_name_to_obj[r_name] = run_obj

    for p in meta["provenance"]:
        file_row.provenance_records.append(ProvenanceRecord(**p))

    session.flush()  # allocate run ids before we link identifications

    for ident in meta["identifications"]:
        run = run_name_to_obj.get(ident["run_name"])
        if run is None:
            # Identification refers to a run not in the index — skip
            # rather than crash; flag in metadata for audit.
            continue
        file_row.identifications.append(
            Identification(
                run_id=run.id,
                chebi_id=ident["chebi_id"],
                name=ident["name"],
                score=ident["score"],
                spectrum_index=ident["spectrum_index"],
                metadata_json=ident["metadata_json"],
            )
        )

    session.flush()
    session.commit()

    counts = {
        "studies": session.query(Study).filter(Study.file_id == file_row.id).count(),
        "runs": session.query(Run).filter(Run.file_id == file_row.id).count(),
        "identifications": session.query(Identification)
        .filter(Identification.file_id == file_row.id)
        .count(),
        "provenance_records": session.query(ProvenanceRecord)
        .filter(ProvenanceRecord.file_id == file_row.id)
        .count(),
    }

    return RegistrationResult(
        file_id=file_row.id,
        uri=canon,
        file_sha256=file_sha,
        format_version=meta["format_version"],
        features=meta["features"],
        counts=counts,
        was_update=was_update,
    )
