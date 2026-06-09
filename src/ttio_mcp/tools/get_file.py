from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ttio_mcp.tools._helpers import file_to_dict, lookup_file

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "integer", "minimum": 1},
        "uri": {"type": "string"},
    },
    "oneOf": [{"required": ["id"]}, {"required": ["uri"]}],
}


async def handle(session: Session, args: dict[str, Any]) -> dict[str, Any]:
    f = lookup_file(session, id_or_uri=args)
    out = file_to_dict(f, include_counts=True)
    out["studies"] = [
        {"id": s.id, "title": s.title, "isa_investigation_id": s.isa_investigation_id}
        for s in f.studies
    ]
    out["runs"] = [
        {
            "id": r.id,
            "name": r.name,
            "acquisition_mode": r.acquisition_mode,
            "spectrum_count": r.spectrum_count,
            "instrument_manufacturer": r.instrument_manufacturer,
            "instrument_model": r.instrument_model,
            "polarity": r.polarity,
        }
        for r in f.runs
    ]
    return out
