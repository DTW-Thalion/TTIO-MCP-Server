"""``mpgo_decrypt_file`` — in-place persist-to-disk decryption.

Resolves a catalog entry to a local path, loads the AES-256-GCM key
from the server-side keyring by ``key_id``, and calls
:meth:`mpeg_o.SpectralDataset.decrypt_in_place` (v1.1.1 API) which
rewrites the file to plaintext. The catalog row's
``file_sha256`` / ``content_sha256`` / ``encrypted`` /
``encrypted_algorithm`` are refreshed.

Cloud URIs are rejected — the MCP server does not download, decrypt,
and re-upload remote files.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from mpeg_o_mcp.catalog import (
    CatalogError,
    InvalidURI,
    ResolveFailed,
    resolve_local_path,
)
from mpeg_o_mcp.hashes import hash_content_sha256, hash_file_sha256
from mpeg_o_mcp.keyring import Keyring
from mpeg_o_mcp.tools._helpers import lookup_file

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "integer", "minimum": 1},
        "uri": {"type": "string"},
        "key_id": {
            "type": "string",
            "description": (
                "Keyring id resolved server-side from MPGO_KEYRING_PATH."
            ),
        },
        "as_user": {"type": "string"},
    },
    "required": ["key_id"],
    "oneOf": [{"required": ["id", "key_id"]}, {"required": ["uri", "key_id"]}],
}


class DecryptFailed(CatalogError):
    code = "decrypt_failed"


class NotEncrypted(CatalogError):
    code = "not_encrypted"


class RemoteNotSupported(CatalogError):
    code = "remote_not_supported"


async def handle(
    session: Session,
    args: dict[str, Any],
    *,
    keyring: Keyring,
) -> dict[str, Any]:
    key_id = args["key_id"]

    f = lookup_file(session, id_or_uri={"id": args.get("id"), "uri": args.get("uri")})
    if not f.encrypted:
        raise NotEncrypted(
            f"file id={f.id} is not marked encrypted in the catalog"
        )

    try:
        path = resolve_local_path(f.uri)
    except InvalidURI as exc:
        raise RemoteNotSupported(
            f"decrypt only supports local files; {f.uri} is remote"
        ) from exc
    except ResolveFailed:
        raise

    key = keyring.get(key_id)

    from mpeg_o import SpectralDataset

    try:
        SpectralDataset.decrypt_in_place(path, key)
    except Exception as exc:
        raise DecryptFailed(
            f"decrypt_in_place failed on {path}: {type(exc).__name__}: {exc}"
        ) from exc

    new_file_sha = hash_file_sha256(path)
    new_content_sha = hash_content_sha256(path)

    f.encrypted = False
    f.encrypted_algorithm = None
    f.file_sha256 = new_file_sha
    f.content_sha256 = new_content_sha
    f.last_verified_at = datetime.now(UTC)
    session.commit()

    return {
        "file_id": f.id,
        "uri": f.uri,
        "encrypted": False,
        "encrypted_algorithm": None,
        "key_id": key_id,
        "file_sha256": new_file_sha,
        "content_sha256": new_content_sha,
    }
