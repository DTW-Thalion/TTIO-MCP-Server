from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from mpeg_o_mcp.catalog import ResolveFailed, resolve_local_path
from mpeg_o_mcp.hashes import hash_file_sha256
from mpeg_o_mcp.tools._helpers import lookup_file

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

    try:
        path = resolve_local_path(f.uri)
    except ResolveFailed as exc:
        return {
            "file_id": f.id,
            "uri": f.uri,
            "resolved": False,
            "error": str(exc),
        }

    new_sha = hash_file_sha256(path)
    drift = new_sha != f.file_sha256

    f.last_verified_at = datetime.now(UTC)
    if drift:
        f.file_sha256 = new_sha
    session.commit()

    return {
        "file_id": f.id,
        "uri": f.uri,
        "resolved": True,
        "drift": drift,
        "file_sha256": new_sha,
        "last_verified_at": f.last_verified_at.isoformat(),
    }
