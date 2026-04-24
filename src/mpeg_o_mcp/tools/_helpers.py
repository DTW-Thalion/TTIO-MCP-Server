from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpeg_o_mcp.catalog import CatalogError, NotFound, canonical_uri, resolve_local_path
from mpeg_o_mcp.db.models import File


def lookup_file(session: Session, *, id_or_uri: dict[str, Any]) -> File:
    """Resolve a file row from {"id": int} or {"uri": str}.

    Accepts ``id`` (integer primary key) or ``uri`` (bare path or file://).
    Paths are canonicalised before lookup so either form hits the same row.
    """
    if "id" in id_or_uri and id_or_uri["id"] is not None:
        fid = int(id_or_uri["id"])
        row = session.get(File, fid)
        if row is None:
            raise NotFound(f"no file with id={fid}")
        return row
    if "uri" in id_or_uri and id_or_uri["uri"]:
        raw = id_or_uri["uri"]
        # Canonicalise to the same form register_file stores.
        try:
            path = resolve_local_path(raw)
            canon = canonical_uri(path)
        except CatalogError:
            # File on disk may have been removed; try raw lookup anyway.
            canon = raw
        row = session.execute(select(File).where(File.uri == canon)).scalar_one_or_none()
        if row is None:
            raise NotFound(f"no file with uri={canon!r}")
        return row
    raise NotFound("provide either 'id' or 'uri'")


def file_to_dict(f: File, *, include_counts: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": f.id,
        "uri": f.uri,
        "display_name": f.display_name,
        "file_sha256": f.file_sha256,
        "content_sha256": f.content_sha256,
        "format_version": f.format_version,
        "features": (f.features or {}).get("list", []),
        "encrypted": f.encrypted,
        "signed": f.signed,
        "registered_at": f.registered_at.isoformat() if f.registered_at else None,
        "last_verified_at": f.last_verified_at.isoformat() if f.last_verified_at else None,
        "registered_by": f.registered_by,
        "owner_user_id": f.owner_user_id,
    }
    if include_counts:
        out["counts"] = {
            "studies": len(f.studies),
            "runs": len(f.runs),
            "identifications": len(f.identifications),
            "quantifications": len(f.quantifications),
            "provenance_records": len(f.provenance_records),
        }
    return out
