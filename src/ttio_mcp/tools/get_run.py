"""Per-run detail with inline identifications + quantifications."""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ttio_mcp.catalog import NotFound
from ttio_mcp.db.models import Identification, Quantification, Run

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "run_id": {"type": "integer", "minimum": 1},
        "file_id": {"type": "integer", "minimum": 1},
        "run_name": {"type": "string"},
    },
    "oneOf": [
        {"required": ["run_id"]},
        {"required": ["file_id", "run_name"]},
    ],
}


async def handle(session: Session, args: dict[str, Any]) -> dict[str, Any]:
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

    idents = session.execute(
        select(Identification)
        .where(Identification.run_id == run.id)
        .order_by(Identification.score.desc(), Identification.id.asc())
    ).scalars().all()

    quants = session.execute(
        select(Quantification).where(
            Quantification.file_id == run.file_id,
            or_(
                Quantification.sample_ref == run.name,
                Quantification.sample_ref.is_(None),
            ),
        ).order_by(Quantification.id.asc())
    ).scalars().all()

    return {
        "id": run.id,
        "file_id": run.file_id,
        "name": run.name,
        "acquisition_mode": run.acquisition_mode,
        "spectrum_count": run.spectrum_count,
        "instrument_manufacturer": run.instrument_manufacturer,
        "instrument_model": run.instrument_model,
        "polarity": run.polarity,
        "nucleus_type": (run.metadata_json or {}).get("nucleus_type"),
        "channel_names": (run.metadata_json or {}).get("channel_names", []),
        "identifications": [
            {
                "id": i.id,
                "chebi_id": i.chebi_id,
                "name": i.name,
                "score": i.score,
                "spectrum_index": i.spectrum_index,
                "evidence_chain": (i.metadata_json or {}).get("evidence_chain", []),
            }
            for i in idents
        ],
        "quantifications": [
            {
                "id": q.id,
                "chebi_id": q.chebi_id,
                "name": q.name,
                "sample_ref": q.sample_ref,
                "abundance": q.abundance,
                "normalization_method": q.normalization_method,
            }
            for q in quants
        ],
    }
