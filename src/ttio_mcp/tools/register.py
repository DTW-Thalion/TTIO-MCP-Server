from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ttio_mcp.catalog import register_file
from ttio_mcp.tools._fsspec_defaults import merged_fsspec_kwargs

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "uri": {
            "type": "string",
            "description": (
                "Registration target. Accepts file:// URIs, bare absolute "
                "paths, or cloud URIs (s3://, https://, gs://, az://). "
                "Remote URIs stream lazily via fsspec; registration hashes "
                "the full object, so large cloud files take time."
            ),
        },
        "display_name": {"type": "string"},
        "as_user": {
            "type": "string",
            "description": (
                "Username for ownership. Must already exist in the users "
                "table — unknown names are rejected with unknown_user. "
                "Defaults to the seeded 'system' user."
            ),
        },
        "fsspec_kwargs": {
            "type": "object",
            "description": (
                "Optional keyword arguments forwarded to fsspec.open for "
                "remote URIs. Shallow-merged on top of TTIO_MCP_FSSPEC_KWARGS "
                "(per-call keys win). Typical keys: anon, key, secret, profile, "
                "client_kwargs.endpoint_url."
            ),
            "additionalProperties": True,
        },
    },
    "required": ["uri"],
}


async def handle(session: Session, args: dict[str, Any]) -> dict[str, Any]:
    uri = args["uri"]
    display_name = args.get("display_name")
    as_user = args.get("as_user")
    fsspec_kwargs = merged_fsspec_kwargs(args.get("fsspec_kwargs"))
    result = register_file(
        session,
        uri,
        display_name=display_name,
        as_user=as_user,
        fsspec_kwargs=fsspec_kwargs,
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
