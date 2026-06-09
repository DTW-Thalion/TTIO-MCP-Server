"""``ttio_verify_signature`` — HMAC-SHA256 signature verification.

Resolves a catalog entry to a local path, loads the HMAC-SHA256 key
from the server-side keyring, opens the ``.mpgo`` via h5py in ``r``
mode, and calls :func:`mpeg_o.signatures.verify_dataset` on every
dataset that carries an ``@ttio_signature`` attribute.

Returns a per-dataset verdict map plus an aggregate ``valid`` boolean
that is ``true`` iff every signed dataset verified under the key. If
the file has no signed datasets the tool returns ``valid=false`` with
the new ``not_signed`` error code so callers never get a false
positive from an unsigned file.

Cloud URIs are rejected; encrypted files are rejected (the canonical
byte stream depends on plaintext values).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ttio_mcp.catalog import (
    CatalogError,
    InvalidURI,
    ResolveFailed,
    resolve_local_path,
)
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
                "Must reference an hmac-sha256 key."
            ),
        },
    },
    "required": ["key_id"],
    "oneOf": [{"required": ["id", "key_id"]}, {"required": ["uri", "key_id"]}],
}


class VerifyFailed(CatalogError):
    code = "verify_failed"


class RemoteNotSupported(CatalogError):
    code = "remote_not_supported"


class NotSigned(CatalogError):
    code = "not_signed"


class AlreadyEncrypted(CatalogError):
    code = "already_encrypted"


async def handle(
    session: Session,
    args: dict[str, Any],
    *,
    keyring: Keyring,
) -> dict[str, Any]:
    key_id = args["key_id"]

    f = lookup_file(session, id_or_uri={"id": args.get("id"), "uri": args.get("uri")})
    if f.encrypted:
        raise AlreadyEncrypted(
            f"file id={f.id} is encrypted; decrypt before verifying"
        )

    try:
        path = resolve_local_path(f.uri)
    except InvalidURI as exc:
        raise RemoteNotSupported(
            f"verify only supports local files; {f.uri} is remote"
        ) from exc
    except ResolveFailed:
        raise

    key = keyring.get(key_id, expected_algorithm=HMAC_SHA256)

    verdicts = _verify_all_signed_datasets(path, key)
    if not verdicts:
        raise NotSigned(
            f"file id={f.id} has no signed datasets "
            f"(no @ttio_signature attributes found)"
        )

    all_valid = all(v for v in verdicts.values())

    return {
        "file_id": f.id,
        "uri": f.uri,
        "valid": all_valid,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "signed_at": f.signed_at.isoformat() if f.signed_at else None,
        "signed_by": f.signed_by,
        "key_id": key_id,
        "verified_datasets": verdicts,
        "verified_dataset_count": len(verdicts),
    }


def _verify_all_signed_datasets(path: Path, key: bytes) -> dict[str, bool]:
    """Walk every signed dataset in ``path``; return ``{hdf5_path: bool}``.

    A dataset is "signed" iff its ``@ttio_signature`` attribute is
    present. Empty return means nothing was signed.
    """
    import h5py
    from mpeg_o import signatures
    from mpeg_o.signatures import SIGNATURE_ATTR

    verdicts: dict[str, bool] = {}
    try:
        with h5py.File(str(path), "r") as hfile:

            def _visit(name: str, obj: Any) -> None:
                if not isinstance(obj, h5py.Dataset):
                    return
                if SIGNATURE_ATTR not in obj.attrs:
                    return
                hdf5_path = "/" + name
                try:
                    ok = signatures.verify_dataset(
                        obj, key, algorithm=SIGNATURE_ALGORITHM
                    )
                except Exception:
                    ok = False
                verdicts[hdf5_path] = bool(ok)

            hfile.visititems(_visit)
    except Exception as exc:
        raise VerifyFailed(
            f"verify failed on {path}: {type(exc).__name__}: {exc}"
        ) from exc
    return dict(sorted(verdicts.items()))
