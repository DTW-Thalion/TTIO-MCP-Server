"""``ttio_push_file`` — upload a local .mpgo to a cloud URI, optionally encrypt-on-upload.

Reads a local ``.mpgo``, optionally encrypts a throwaway copy with an
AES-256-GCM key from the server-side keyring, streams the bytes to a
writable cloud destination via fsspec, then registers the uploaded
object in the catalog.

The local source is never modified. To post-hoc encrypt a file that
already lives in the cloud, the workflow is manual — pull it down,
encrypt with :func:`ttio_encrypt_file`, push it back with this tool.

Only writable cloud schemes are accepted for ``remote_uri``:
``s3://``, ``gs://``, ``gcs://``, ``abfs://``, ``abfss://``, ``az://``.
``http://``/``https://`` and local schemes are rejected up front.
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from ttio_mcp.catalog import (
    CatalogError,
    InvalidURI,
    register_file,
    resolve_local_path,
)
from ttio_mcp.db.models import File
from ttio_mcp.keyring import AES_256_GCM, Keyring
from ttio_mcp.tools._fsspec_defaults import merged_fsspec_kwargs
from ttio_mcp.tools.encrypt_file import ENCRYPTION_LEVELS

WRITABLE_REMOTE_SCHEMES = {"s3", "gs", "gcs", "abfs", "abfss", "az"}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "local_uri": {
            "type": "string",
            "description": (
                "Local source file: file:// URI or absolute path. "
                "The file on disk is never modified by this tool."
            ),
        },
        "remote_uri": {
            "type": "string",
            "description": (
                "Destination cloud URI. Must use a writable scheme: "
                "s3://, gs://, gcs://, abfs://, abfss://, az://."
            ),
        },
        "key_id": {
            "type": "string",
            "description": (
                "Optional keyring id. When set, a throwaway temp copy "
                "is encrypted with AES-256-GCM before upload; the "
                "ciphertext is what lands at remote_uri."
            ),
        },
        "level": {
            "type": "string",
            "enum": ENCRYPTION_LEVELS,
            "description": (
                "TTI-O EncryptionLevel. Only consulted when key_id is set. "
                "Defaults to DATASET_GROUP."
            ),
        },
        "as_user": {
            "type": "string",
            "description": (
                "Ownership for the new catalog row. Defaults to 'system'."
            ),
        },
        "fsspec_kwargs": {
            "type": "object",
            "additionalProperties": True,
            "description": (
                "Forwarded to fsspec.open for both the upload and the "
                "post-upload register. Shallow-merged on top of "
                "TTIO_MCP_FSSPEC_KWARGS."
            ),
        },
    },
    "required": ["local_uri", "remote_uri"],
}


class SchemeNotWritable(CatalogError):
    code = "scheme_not_writable"


class UploadFailed(CatalogError):
    code = "upload_failed"


class EncryptFailed(CatalogError):
    code = "encrypt_failed"


async def handle(
    session: Session,
    args: dict[str, Any],
    *,
    keyring: Keyring,
) -> dict[str, Any]:
    local_uri = args["local_uri"]
    remote_uri = args["remote_uri"]
    key_id = args.get("key_id")
    level_name = args.get("level", "DATASET_GROUP")
    as_user = args.get("as_user")
    fsspec_kwargs = merged_fsspec_kwargs(args.get("fsspec_kwargs"))

    _check_writable_scheme(remote_uri)

    try:
        local_path = resolve_local_path(local_uri)
    except InvalidURI as exc:
        raise SchemeNotWritable(
            f"local_uri must be a local path or file:// URI: {local_uri}"
        ) from exc

    staged: Path | None = None
    try:
        if key_id:
            key = keyring.get(key_id, expected_algorithm=AES_256_GCM)
            staged = _stage_encrypted_copy(local_path, key, level_name)
            upload_source: Path = staged
        else:
            upload_source = local_path

        _upload(upload_source, remote_uri, fsspec_kwargs)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)

    result = register_file(
        session,
        remote_uri,
        as_user=as_user,
        fsspec_kwargs=fsspec_kwargs,
    )

    if key_id:
        file_row = session.get(File, result.file_id)
        file_row.encrypted = True
        file_row.encrypted_algorithm = AES_256_GCM
        file_row.last_verified_at = datetime.now(UTC)
        session.commit()

    return {
        "file_id": result.file_id,
        "uri": result.uri,
        "remote_uri": result.uri,
        "file_sha256": result.file_sha256,
        "encrypted": bool(key_id),
        "encrypted_algorithm": AES_256_GCM if key_id else None,
        "key_id": key_id,
        "counts": result.counts,
        "was_update": result.was_update,
    }


def _check_writable_scheme(remote_uri: str) -> None:
    scheme = urlparse(remote_uri).scheme.lower()
    if scheme not in WRITABLE_REMOTE_SCHEMES:
        allowed = ", ".join(sorted(WRITABLE_REMOTE_SCHEMES))
        raise SchemeNotWritable(
            f"remote_uri scheme {scheme!r} is not a writable cloud scheme; "
            f"use one of: {allowed}"
        )


def _stage_encrypted_copy(src: Path, key: bytes, level_name: str) -> Path:
    """Copy ``src`` into a tempfile and encrypt the copy in place.

    The tempfile outlives this function — the caller is responsible for
    deleting it after upload.
    """
    from mpeg_o import SpectralDataset
    from mpeg_o.enums import EncryptionLevel

    try:
        level = EncryptionLevel[level_name]
    except KeyError as exc:
        raise EncryptFailed(f"unknown encryption level {level_name!r}") from exc

    handle = tempfile.NamedTemporaryFile(
        prefix="mpgo-push-", suffix=".mpgo", delete=False
    )
    staged = Path(handle.name)
    handle.close()
    try:
        shutil.copyfile(src, staged)
        dataset = SpectralDataset.open(staged, writable=True)
        try:
            dataset.encrypt_with_key(key, level)
        finally:
            dataset.close()
    except Exception as exc:
        staged.unlink(missing_ok=True)
        raise EncryptFailed(
            f"encrypt-on-push failed for {src}: {type(exc).__name__}: {exc}"
        ) from exc
    return staged


def _upload(src: Path, dest_uri: str, fsspec_kwargs: dict[str, Any]) -> None:
    import fsspec

    try:
        with (
            open(src, "rb") as sfp,
            fsspec.open(dest_uri, "wb", **fsspec_kwargs) as dfp,
        ):
            shutil.copyfileobj(sfp, dfp, length=1024 * 1024)
    except Exception as exc:
        raise UploadFailed(
            f"upload to {dest_uri} failed: {type(exc).__name__}: {exc}"
        ) from exc
