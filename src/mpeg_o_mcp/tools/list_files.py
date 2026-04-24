from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mpeg_o_mcp.db.models import File, Run, Study
from mpeg_o_mcp.tools._helpers import file_to_dict

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        "offset": {"type": "integer", "minimum": 0, "default": 0},
        "title_contains": {"type": "string"},
        "acquisition_mode": {
            "type": "string",
            "description": "Exact match against any run's acquisition_mode (e.g. 'MS1_DDA', 'NMR_1D').",
        },
    },
}


async def handle(session: Session, args: dict[str, Any]) -> dict[str, Any]:
    limit = int(args.get("limit", 50))
    offset = int(args.get("offset", 0))
    title_contains = args.get("title_contains")
    mode = args.get("acquisition_mode")

    base = select(File)
    total_q = select(func.count(File.id))

    if title_contains:
        subq = select(Study.file_id).where(Study.title.ilike(f"%{title_contains}%"))
        base = base.where(File.id.in_(subq))
        total_q = total_q.where(File.id.in_(subq))

    if mode:
        subq = select(Run.file_id).where(Run.acquisition_mode == mode)
        base = base.where(File.id.in_(subq))
        total_q = total_q.where(File.id.in_(subq))

    total = session.execute(total_q).scalar_one()
    rows = session.execute(
        base.order_by(File.id).limit(limit).offset(offset)
    ).scalars().all()

    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "files": [file_to_dict(r) for r in rows],
    }
