"""Per-file quantification listing with filters."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ttio_mcp.db.models import Quantification
from ttio_mcp.tools._helpers import lookup_file

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "file_id": {"type": "integer", "minimum": 1},
        "uri": {"type": "string"},
        "chebi_id": {"type": "string"},
        "sample_ref": {"type": "string"},
        "min_abundance": {"type": "number"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        "offset": {"type": "integer", "minimum": 0, "default": 0},
    },
    "oneOf": [
        {"required": ["file_id"]},
        {"required": ["uri"]},
    ],
}


async def handle(session: Session, args: dict[str, Any]) -> dict[str, Any]:
    # Normalise to the lookup_file contract ({id} or {uri}).
    lookup_args = dict(args)
    if "file_id" in lookup_args:
        lookup_args["id"] = lookup_args.pop("file_id")
    f = lookup_file(session, id_or_uri=lookup_args)
    limit = int(args.get("limit", 50))
    offset = int(args.get("offset", 0))

    base = select(Quantification).where(Quantification.file_id == f.id)
    if "chebi_id" in args:
        base = base.where(Quantification.chebi_id == args["chebi_id"])
    if "sample_ref" in args:
        base = base.where(Quantification.sample_ref == args["sample_ref"])
    if "min_abundance" in args:
        base = base.where(Quantification.abundance >= float(args["min_abundance"]))

    total = session.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()

    rows = session.execute(
        base.order_by(Quantification.id.asc()).limit(limit).offset(offset)
    ).scalars().all()

    return {
        "file_id": f.id,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "quantifications": [
            {
                "id": q.id,
                "chebi_id": q.chebi_id,
                "name": q.name,
                "sample_ref": q.sample_ref,
                "abundance": q.abundance,
                "normalization_method": q.normalization_method,
            }
            for q in rows
        ],
    }
