"""Cross-file identification search."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ttio_mcp.db.models import File, Identification, Run

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "chebi_id": {"type": "string"},
        "name_contains": {"type": "string"},
        "min_score": {"type": "number", "minimum": 0, "maximum": 1},
        "acquisition_mode": {"type": "string"},
        "file_id": {"type": "integer", "minimum": 1},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        "offset": {"type": "integer", "minimum": 0, "default": 0},
    },
}


async def handle(session: Session, args: dict[str, Any]) -> dict[str, Any]:
    limit = int(args.get("limit", 50))
    offset = int(args.get("offset", 0))

    base = (
        select(Identification, Run, File)
        .join(Run, Identification.run_id == Run.id)
        .join(File, Identification.file_id == File.id)
    )

    if "chebi_id" in args:
        base = base.where(Identification.chebi_id == args["chebi_id"])
    if "name_contains" in args:
        base = base.where(Identification.name.like(f"%{args['name_contains']}%"))
    if "min_score" in args:
        base = base.where(Identification.score >= float(args["min_score"]))
    if "acquisition_mode" in args:
        base = base.where(Run.acquisition_mode == args["acquisition_mode"])
    if "file_id" in args:
        base = base.where(Identification.file_id == int(args["file_id"]))

    total = session.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()

    ordered = base.order_by(Identification.score.desc(), Identification.id.asc())
    rows = session.execute(ordered.limit(limit).offset(offset)).all()

    identifications = [
        {
            "id": ident.id,
            "file_id": ident.file_id,
            "file_uri": f.uri,
            "run_id": ident.run_id,
            "run_name": r.name,
            "acquisition_mode": r.acquisition_mode,
            "chebi_id": ident.chebi_id,
            "name": ident.name,
            "score": ident.score,
            "spectrum_index": ident.spectrum_index,
            "evidence_chain": (ident.metadata_json or {}).get("evidence_chain", []),
        }
        for (ident, r, f) in rows
    ]

    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "identifications": identifications,
    }
