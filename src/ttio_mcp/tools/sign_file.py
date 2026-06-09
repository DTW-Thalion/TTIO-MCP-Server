"""``ttio_sign_file`` — HMAC-SHA256 dataset signatures.

Resolves a catalog entry to a local path, loads the HMAC-SHA256 key
from the server-side keyring by ``key_id``, opens the ``.mpgo`` file
via h5py in ``r+`` mode, walks every signal-channel dataset under
``study/*/{ms_runs,nmr_runs}/*/signal_channels/*_values``, and calls
:func:`mpeg_o.signatures.sign_dataset` on each.

Each signed dataset gets an ``@ttio_signature`` VL-string attribute
whose value is prefixed with ``v2:`` (the TTI-O canonical-form
HMAC-SHA256 tag). The existing ``@ttio_signature`` attribute — if any
— is replaced, so re-signing with a new key is idempotent at the
file level.

After signing the on-disk bytes change (new VL attrs); the catalog
row's ``file_sha256`` / ``content_sha256`` / ``signed`` /
``signature_algorithm`` / ``signed_at`` / ``signed_by`` are refreshed.

Cloud URIs are rejected — the MCP server does not download, sign,
and re-upload remote files. Encrypted files are rejected here too:
signing operates on dataset byte layout, and the canonical form
depends on plaintext values being present. Decrypt first if needed.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ttio_mcp.catalog import (
    CatalogError,
    InvalidURI,
    ResolveFailed,
    _resolve_as_user,
    resolve_local_path,
)
from ttio_mcp.hashes import hash_content_sha256, hash_file_sha256
from ttio_mcp.keyring import HMAC_SHA256, Keyring
from ttio_mcp.tools._helpers import lookup_file

SIGNATURE_ALGORITHM = HMAC_SHA256

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "integer", "minimum": 1},
        "uri": {"type": "string"},
        "key_id": {
            "type": "string",
            "description": (
                "Keyring id resolved server-side from TTIO_KEYRING_PATH. "
                "Must reference an hmac-sha256 key; raw bytes never cross "
                "the MCP wire."
            ),
        },
        "as_user": {"type": "string"},
    },
    "required": ["key_id"],
    "oneOf": [{"required": ["id", "key_id"]}, {"required": ["uri", "key_id"]}],
}


class SignFailed(CatalogError):
    code = "sign_failed"


class RemoteNotSupported(CatalogError):
    code = "remote_not_supported"


class AlreadyEncrypted(CatalogError):
    code = "already_encrypted"


class NothingToSign(CatalogError):
    code = "nothing_to_sign"


async def handle(
    session: Session,
    args: dict[str, Any],
    *,
    keyring: Keyring,
) -> dict[str, Any]:
    key_id = args["key_id"]
    as_user = args.get("as_user")

    f = lookup_file(session, id_or_uri={"id": args.get("id"), "uri": args.get("uri")})
    if f.encrypted:
        raise AlreadyEncrypted(
            f"file id={f.id} is encrypted "
            f"(algorithm={f.encrypted_algorithm or 'unknown'}); "
            f"decrypt before signing — canonical-form HMAC requires plaintext"
        )

    try:
        path = resolve_local_path(f.uri)
    except InvalidURI as exc:
        raise RemoteNotSupported(
            f"sign only supports local files; {f.uri} is remote"
        ) from exc
    except ResolveFailed:
        raise

    key = keyring.get(key_id, expected_algorithm=HMAC_SHA256)

    signed_paths = _sign_all_signal_datasets(path, key)
    if not signed_paths:
        raise NothingToSign(
            f"no signal_channels/*_values datasets found in {path}; "
            f"file may be empty or not an .mpgo"
        )

    new_file_sha = hash_file_sha256(path)
    new_content_sha = hash_content_sha256(path)

    signed_by_id = _resolve_as_user(session, as_user)
    now = datetime.now(UTC)

    f.signed = True
    f.signature_algorithm = SIGNATURE_ALGORITHM
    f.signed_at = now
    f.signed_by = signed_by_id
    f.file_sha256 = new_file_sha
    f.content_sha256 = new_content_sha
    f.last_verified_at = now
    session.commit()

    return {
        "file_id": f.id,
        "uri": f.uri,
        "signed": True,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "signed_at": now.isoformat(),
        "signed_by": signed_by_id,
        "key_id": key_id,
        "signed_datasets": signed_paths,
        "signed_dataset_count": len(signed_paths),
        "file_sha256": new_file_sha,
        "content_sha256": new_content_sha,
    }


def _sign_all_signal_datasets(path: Path, key: bytes) -> list[str]:
    """Open ``path`` in h5py r+ mode and sign every signal-channel dataset.

    Returns the sorted list of HDF5 paths that were signed.
    """
    import h5py
    from mpeg_o import signatures

    signed: list[str] = []
    try:
        with h5py.File(str(path), "r+") as hfile:
            dataset_paths = _find_signal_datasets(hfile)
            for dpath in dataset_paths:
                ds = hfile[dpath]
                if not isinstance(ds, h5py.Dataset):  # pragma: no cover - guard
                    continue
                signatures.sign_dataset(ds, key, algorithm=SIGNATURE_ALGORITHM)
                signed.append(dpath)
    except Exception as exc:
        raise SignFailed(
            f"sign failed on {path}: {type(exc).__name__}: {exc}"
        ) from exc
    signed.sort()
    return signed


def _find_signal_datasets(hfile: Any) -> list[str]:
    """Collect every ``*_values`` dataset under a ``signal_channels`` group.

    Matches both MS (``study/*/ms_runs/<run>/signal_channels/*_values``)
    and NMR (``study/*/nmr_runs/<run>/signal_channels/*_values``) layouts.
    """
    import h5py

    paths: list[str] = []

    def _visit(name: str, obj: Any) -> None:
        if not isinstance(obj, h5py.Dataset):
            return
        if "/signal_channels/" not in "/" + name:
            return
        if not name.endswith("_values"):
            return
        paths.append("/" + name)

    hfile.visititems(_visit)
    return paths


