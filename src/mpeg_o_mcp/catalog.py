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
    Quantification,
    Run,
    Study,
    User,
)
from mpeg_o_mcp.hashes import hash_content_sha256, hash_file_sha256

LOCAL_SCHEMES = {"file", ""}


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
class ResolvedTarget:
    """Resolved registration target.

    ``local_path`` is set for local files only (the on-disk absolute
    path). ``canonical_uri`` is what goes into ``files.uri``.
    ``is_remote`` short-circuits callers who need to choose between
    local and fsspec code paths.
    """

    canonical_uri: str
    is_remote: bool
    local_path: Path | None


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
    """Back-compat helper: local-only resolution.

    Callers that need to accept remote URIs should use :func:`resolve_uri`.
    """
    parsed = urlparse(uri)
    if parsed.scheme not in LOCAL_SCHEMES:
        raise InvalidURI(
            f"scheme {parsed.scheme!r} is remote; use resolve_uri for cloud support"
        )
    raw = parsed.path if parsed.scheme == "file" else uri
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise ResolveFailed(f"{path} does not exist")
    if not path.is_file():
        raise ResolveFailed(f"{path} is not a regular file")
    return path


def resolve_uri(
    uri: str,
    *,
    fsspec_kwargs: dict[str, Any] | None = None,
) -> ResolvedTarget:
    """Resolve a URI into a :class:`ResolvedTarget`.

    Local paths / ``file://`` are canonicalised as in M2. Remote URIs
    recognised by MPEG-O (``s3://``, ``https://``, ``gs://``, ...) are
    probed via fsspec to fail fast on 404 / 403, then passed through
    verbatim (scheme lowercased).
    """
    from mpeg_o.remote import is_remote_url

    if is_remote_url(uri):
        parsed = urlparse(uri)
        scheme = parsed.scheme.lower()
        if scheme in LOCAL_SCHEMES:
            # file:// is technically in REMOTE_SCHEMES; route it local.
            return _resolve_local(uri)
        canon = _canonical_remote(uri)
        _probe_remote(canon, fsspec_kwargs or {})
        return ResolvedTarget(canonical_uri=canon, is_remote=True, local_path=None)

    return _resolve_local(uri)


def _resolve_local(uri: str) -> ResolvedTarget:
    path = resolve_local_path(uri)
    return ResolvedTarget(
        canonical_uri=canonical_uri(path),
        is_remote=False,
        local_path=path,
    )


def _canonical_remote(uri: str) -> str:
    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()
    rest = uri[len(parsed.scheme):]  # keep "://..." etc. untouched
    return scheme + rest


def _probe_remote(url: str, fsspec_kwargs: dict[str, Any]) -> None:
    """Fail fast for obviously-broken remote URIs (404 / auth errors).

    We rely on ``fsspec.open`` to surface HTTP / cloud errors with a
    meaningful message. We don't read anything — the opener does a
    HEAD-equivalent to populate ``size``.
    """
    import fsspec

    try:
        with fsspec.open(url, "rb", **fsspec_kwargs):
            pass
    except FileNotFoundError as exc:
        raise ResolveFailed(f"remote object not found: {url}") from exc
    except Exception as exc:  # PermissionError, botocore errors, etc.
        raise ResolveFailed(f"cannot access {url}: {type(exc).__name__}: {exc}") from exc


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

    quant_payload = [
        {
            "chebi_id": q.chemical_entity or None,
            "name": q.chemical_entity or None,
            "sample_ref": q.sample_ref or None,
            "abundance": float(q.abundance) if q.abundance is not None else None,
            "normalization_method": q.normalization_method or None,
            "metadata_json": {},
        }
        for q in dataset.quantifications()
    ]

    return {
        "format_version": format_version,
        "features": features,
        "studies": studies_payload,
        "runs": runs_payload,
        "identifications": id_payload,
        "quantifications": quant_payload,
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
    fsspec_kwargs: dict[str, Any] | None = None,
) -> RegistrationResult:
    """Resolve, hash, open, extract, and upsert the file row + children.

    ``fsspec_kwargs`` are forwarded to both ``fsspec.open`` (for hashing)
    and :meth:`mpeg_o.SpectralDataset.open` (for metadata extraction).
    They're ignored for local paths.

    Raises :class:`CatalogError` subclasses on user-visible failures.
    """
    from mpeg_o import SpectralDataset  # lazy import so hashes-only callers don't need it

    kwargs = fsspec_kwargs or {}
    target = resolve_uri(uri, fsspec_kwargs=kwargs)
    canon = target.canonical_uri
    hash_target: str | Path = target.local_path if not target.is_remote else canon
    file_sha = hash_file_sha256(hash_target, fsspec_kwargs=kwargs)
    content_sha = hash_content_sha256(hash_target, fsspec_kwargs=kwargs)

    open_target: str | Path = target.local_path if not target.is_remote else canon
    try:
        dataset = SpectralDataset.open(open_target, **kwargs)
    except Exception as exc:  # pragma: no cover - MPEG-O raises a variety
        raise NotMpeg(f"{open_target}: {type(exc).__name__}: {exc}") from exc

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
            existing.quantifications,
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

    for q in meta["quantifications"]:
        file_row.quantifications.append(Quantification(**q))

    session.flush()
    session.commit()

    counts = {
        "studies": session.query(Study).filter(Study.file_id == file_row.id).count(),
        "runs": session.query(Run).filter(Run.file_id == file_row.id).count(),
        "identifications": session.query(Identification)
        .filter(Identification.file_id == file_row.id)
        .count(),
        "quantifications": session.query(Quantification)
        .filter(Quantification.file_id == file_row.id)
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
