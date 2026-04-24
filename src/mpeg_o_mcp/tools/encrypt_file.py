"""``mpgo_encrypt_file`` — in-place AES-256-GCM intensity encryption.

Resolves a catalog entry to a local path, loads the AES-256-GCM key
from the server-side keyring by ``key_id``, and calls
:meth:`mpeg_o.SpectralDataset.encrypt_with_key`. The on-disk bytes
change, so the catalog row's ``file_sha256`` / ``content_sha256`` /
``encrypted`` / ``encrypted_algorithm`` are refreshed.

Cloud URIs are rejected — the MCP server does not download, encrypt,
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
from mpeg_o_mcp.keyring import AES_256_GCM, Keyring
from mpeg_o_mcp.tools._helpers import lookup_file

ENCRYPTION_LEVELS = ["DATASET_GROUP", "DATASET", "DESCRIPTOR_STREAM", "ACCESS_UNIT"]

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "integer", "minimum": 1},
        "uri": {"type": "string"},
        "key_id": {
            "type": "string",
            "description": (
                "Keyring id resolved server-side from MPGO_KEYRING_PATH. "
                "The raw key is never transmitted through MCP."
            ),
        },
        "level": {
            "type": "string",
            "enum": ENCRYPTION_LEVELS,
            "description": (
                "MPEG-O EncryptionLevel. Defaults to DATASET_GROUP "
                "(per-run intensity ciphertext, the common case)."
            ),
        },
        "as_user": {"type": "string"},
    },
    "required": ["key_id"],
    "oneOf": [{"required": ["id", "key_id"]}, {"required": ["uri", "key_id"]}],
}


class EncryptFailed(CatalogError):
    code = "encrypt_failed"


class AlreadyEncrypted(CatalogError):
    code = "already_encrypted"


class RemoteNotSupported(CatalogError):
    code = "remote_not_supported"


async def handle(
    session: Session,
    args: dict[str, Any],
    *,
    keyring: Keyring,
) -> dict[str, Any]:
    key_id = args["key_id"]
    level_name = args.get("level", "DATASET_GROUP")

    f = lookup_file(session, id_or_uri={"id": args.get("id"), "uri": args.get("uri")})
    if f.encrypted:
        raise AlreadyEncrypted(
            f"file id={f.id} is already encrypted "
            f"(algorithm={f.encrypted_algorithm or 'unknown'}); "
            f"call mpgo_decrypt_file first if you want to re-key"
        )

    try:
        path = resolve_local_path(f.uri)
    except InvalidURI as exc:
        raise RemoteNotSupported(
            f"encrypt only supports local files; {f.uri} is remote"
        ) from exc
    except ResolveFailed:
        raise

    key = keyring.get(key_id, expected_algorithm=AES_256_GCM)

    from mpeg_o import SpectralDataset
    from mpeg_o.enums import EncryptionLevel

    try:
        level = EncryptionLevel[level_name]
    except KeyError as exc:
        raise EncryptFailed(f"unknown encryption level {level_name!r}") from exc

    try:
        dataset = SpectralDataset.open(path, writable=True)
    except Exception as exc:
        raise EncryptFailed(
            f"cannot open {path}: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        dataset.encrypt_with_key(key, level)
    except Exception as exc:
        dataset.close()
        raise EncryptFailed(
            f"encrypt_with_key failed on {path}: {type(exc).__name__}: {exc}"
        ) from exc
    dataset.close()

    new_file_sha = hash_file_sha256(path)
    new_content_sha = hash_content_sha256(path)

    f.encrypted = True
    f.encrypted_algorithm = AES_256_GCM
    f.file_sha256 = new_file_sha
    f.content_sha256 = new_content_sha
    f.last_verified_at = datetime.now(UTC)
    session.commit()

    return {
        "file_id": f.id,
        "uri": f.uri,
        "encrypted": True,
        "encrypted_algorithm": AES_256_GCM,
        "level": level_name,
        "key_id": key_id,
        "file_sha256": new_file_sha,
        "content_sha256": new_content_sha,
    }
