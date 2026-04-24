from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from mpeg_o_mcp.catalog import register_file

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "uri": {
            "type": "string",
            "description": "file:// URI or bare absolute path to the .mpgo file",
        },
        "display_name": {"type": "string"},
        "as_user": {
            "type": "string",
            "description": "Username for ownership. Auto-created if unknown. "
                           "Defaults to the seeded 'system' user. Real auth lands in M4.",
        },
    },
    "required": ["uri"],
}


async def handle(session: Session, args: dict[str, Any]) -> dict[str, Any]:
    uri = args["uri"]
    display_name = args.get("display_name")
    as_user = args.get("as_user")
    result = register_file(
        session, uri, display_name=display_name, as_user=as_user
    )
    return {
        "file_id": result.file_id,
        "uri": result.uri,
        "file_sha256": result.file_sha256,
        "format_version": result.format_version,
        "features": result.features,
        "counts": result.counts,
        "was_update": result.was_update,
    }
