"""M7: sign → verify round-trip + negative paths."""
from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests._fixtures import build_ms_fixture
from ttio_mcp.keyring import (
    AES_256_GCM,
    AES_256_GCM_KEY_LEN,
    HMAC_SHA256,
    AlgorithmMismatch,
    Keyring,
)
from ttio_mcp.tools import encrypt_file as ef
from ttio_mcp.tools import sign_file as sf
from ttio_mcp.tools import verify_signature as vf
from ttio_mcp.tools.get_file import handle as handle_get_file
from ttio_mcp.tools.register import handle as handle_register


def _write_hmac_keyring(
    path: Path,
    entries: dict[str, bytes],
) -> Path:
    path.write_text(
        json.dumps(
            {
                "keys": {
                    kid: {
                        "value": base64.b64encode(raw).decode("ascii"),
                        "algorithm": HMAC_SHA256,
                        "description": f"test key {kid}",
                    }
                    for kid, raw in entries.items()
                }
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def ms_file(tmp_path: Path) -> Path:
    return build_ms_fixture(tmp_path / "ms.mpgo")


@pytest.fixture
def keyring_with_hmac(tmp_path: Path) -> tuple[Keyring, str, bytes]:
    raw = os.urandom(32)
    kr_path = _write_hmac_keyring(tmp_path / "kr.json", {"signer": raw})
    return Keyring.from_path(kr_path), "signer", raw


async def test_sign_verify_roundtrip(
    session, ms_file: Path, keyring_with_hmac: tuple[Keyring, str, bytes]
) -> None:
    kr, key_id, _raw = keyring_with_hmac
    reg = await handle_register(session, {"uri": str(ms_file)})
    pre = await handle_get_file(session, {"id": reg["file_id"]})
    assert pre["signed"] is False
    assert pre["signature_algorithm"] is None

    signed = await sf.handle(
        session,
        {"id": reg["file_id"], "key_id": key_id},
        keyring=kr,
    )
    assert signed["signed"] is True
    assert signed["signature_algorithm"] == HMAC_SHA256
    assert signed["signed_dataset_count"] >= 2  # mz_values + intensity_values
    assert all(p.endswith("_values") for p in signed["signed_datasets"])
    assert signed["file_sha256"] != reg["file_sha256"]

    got = await handle_get_file(session, {"id": reg["file_id"]})
    assert got["signed"] is True
    assert got["signature_algorithm"] == HMAC_SHA256
    assert got["signed_at"] is not None
    assert got["signed_by"] == 1  # system user

    verdict = await vf.handle(
        session,
        {"id": reg["file_id"], "key_id": key_id},
        keyring=kr,
    )
    assert verdict["valid"] is True
    assert verdict["verified_dataset_count"] == signed["signed_dataset_count"]
    assert set(verdict["verified_datasets"].keys()) == set(signed["signed_datasets"])
    assert all(verdict["verified_datasets"].values())


async def test_verify_wrong_key_fails(
    session, ms_file: Path, tmp_path: Path
) -> None:
    good = os.urandom(32)
    bad = os.urandom(32)
    kr_path = _write_hmac_keyring(
        tmp_path / "kr.json", {"good": good, "bad": bad}
    )
    kr = Keyring.from_path(kr_path)

    reg = await handle_register(session, {"uri": str(ms_file)})
    await sf.handle(
        session, {"id": reg["file_id"], "key_id": "good"}, keyring=kr
    )

    verdict = await vf.handle(
        session, {"id": reg["file_id"], "key_id": "bad"}, keyring=kr
    )
    assert verdict["valid"] is False
    # Every dataset's verdict should be False under the wrong key.
    assert all(v is False for v in verdict["verified_datasets"].values())


async def test_verify_unsigned_file_raises(
    session, ms_file: Path, keyring_with_hmac: tuple[Keyring, str, bytes]
) -> None:
    kr, key_id, _ = keyring_with_hmac
    reg = await handle_register(session, {"uri": str(ms_file)})
    with pytest.raises(vf.NotSigned):
        await vf.handle(
            session, {"id": reg["file_id"], "key_id": key_id}, keyring=kr
        )


async def test_sign_rejects_aes_key(
    session, ms_file: Path, tmp_path: Path
) -> None:
    # Keyring entry tagged AES-256-GCM — sign_file must refuse because it
    # only accepts hmac-sha256 keys.
    raw = os.urandom(AES_256_GCM_KEY_LEN)
    path = tmp_path / "kr.json"
    path.write_text(
        json.dumps(
            {
                "keys": {
                    "aes-only": {
                        "value": base64.b64encode(raw).decode("ascii"),
                        "algorithm": AES_256_GCM,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    kr = Keyring.from_path(path)
    reg = await handle_register(session, {"uri": str(ms_file)})
    with pytest.raises(AlgorithmMismatch):
        await sf.handle(
            session, {"id": reg["file_id"], "key_id": "aes-only"}, keyring=kr
        )


async def test_sign_rejects_encrypted_file(
    session,
    ms_file: Path,
    tmp_path: Path,
) -> None:
    # Put both an AES key (for encrypting) and an HMAC key (for the sign attempt)
    # in the same keyring. Encrypt first, then confirm signing is rejected.
    aes_raw = os.urandom(AES_256_GCM_KEY_LEN)
    hmac_raw = os.urandom(32)
    kr_path = tmp_path / "kr.json"
    kr_path.write_text(
        json.dumps(
            {
                "keys": {
                    "enc": {
                        "value": base64.b64encode(aes_raw).decode("ascii"),
                        "algorithm": AES_256_GCM,
                    },
                    "sig": {
                        "value": base64.b64encode(hmac_raw).decode("ascii"),
                        "algorithm": HMAC_SHA256,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    kr = Keyring.from_path(kr_path)
    reg = await handle_register(session, {"uri": str(ms_file)})
    await ef.handle(
        session, {"id": reg["file_id"], "key_id": "enc"}, keyring=kr
    )
    with pytest.raises(sf.AlreadyEncrypted):
        await sf.handle(
            session, {"id": reg["file_id"], "key_id": "sig"}, keyring=kr
        )


async def test_sign_remote_rejected(
    session, keyring_with_hmac: tuple[Keyring, str, bytes]
) -> None:
    kr, key_id, _ = keyring_with_hmac
    from ttio_mcp.db.models import File

    row = File(
        uri="s3://fake-bucket/fake.mpgo",
        display_name="fake.mpgo",
        file_sha256="a" * 64,
        content_sha256="b" * 64,
        format_version="1.3",
        features={},
        encrypted=False,
        signed=False,
        registered_by=1,
        owner_user_id=1,
        registered_at=datetime.now(UTC),
    )
    session.add(row)
    session.commit()

    with pytest.raises(sf.RemoteNotSupported):
        await sf.handle(
            session, {"id": row.id, "key_id": key_id}, keyring=kr
        )


async def test_verify_remote_rejected(
    session, keyring_with_hmac: tuple[Keyring, str, bytes]
) -> None:
    kr, key_id, _ = keyring_with_hmac
    from ttio_mcp.db.models import File

    row = File(
        uri="s3://fake-bucket/fake.mpgo",
        display_name="fake.mpgo",
        file_sha256="a" * 64,
        content_sha256="b" * 64,
        format_version="1.3",
        features={},
        encrypted=False,
        signed=True,
        signature_algorithm=HMAC_SHA256,
        registered_by=1,
        owner_user_id=1,
        registered_at=datetime.now(UTC),
    )
    session.add(row)
    session.commit()

    with pytest.raises(vf.RemoteNotSupported):
        await vf.handle(
            session, {"id": row.id, "key_id": key_id}, keyring=kr
        )
