"""M5: end-to-end encrypt → reverify → get_spectrum → decrypt flow."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from ttio_mcp.keyring import AES_256_GCM, AES_256_GCM_KEY_LEN, Keyring
from ttio_mcp.tools import decrypt_file as df
from ttio_mcp.tools import encrypt_file as ef
from ttio_mcp.tools.get_file import handle as handle_get_file
from ttio_mcp.tools.get_spectrum import KeyRequired
from ttio_mcp.tools.get_spectrum import handle as handle_get_spec
from ttio_mcp.tools.register import handle as handle_register
from ttio_mcp.tools.reverify import handle as handle_reverify
from tests._fixtures import build_ms_fixture


def _write_keyring_file(path: Path, key_id: str, raw: bytes) -> Path:
    path.write_text(
        json.dumps(
            {
                "keys": {
                    key_id: {
                        "value": base64.b64encode(raw).decode("ascii"),
                        "algorithm": AES_256_GCM,
                        "description": "test key",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def keyring_with_key(tmp_path: Path) -> tuple[Keyring, str, bytes]:
    raw = os.urandom(AES_256_GCM_KEY_LEN)
    kr_path = _write_keyring_file(tmp_path / "kr.json", "demo", raw)
    return Keyring.from_path(kr_path), "demo", raw


@pytest.fixture
def ms_file(tmp_path: Path) -> Path:
    return build_ms_fixture(tmp_path / "ms.mpgo")


async def test_encrypt_decrypt_roundtrip(
    session, ms_file: Path, keyring_with_key: tuple[Keyring, str, bytes]
) -> None:
    kr, key_id, _raw = keyring_with_key
    reg = await handle_register(session, {"uri": str(ms_file)})
    pre = await handle_get_file(session, {"id": reg["file_id"]})
    assert pre["encrypted"] is False

    # Encrypt.
    enc = await ef.handle(
        session,
        {"id": reg["file_id"], "key_id": key_id},
        keyring=kr,
    )
    assert enc["encrypted"] is True
    assert enc["encrypted_algorithm"] == AES_256_GCM
    assert enc["level"] == "DATASET_GROUP"
    # Hashes changed after in-place rewrite.
    assert enc["file_sha256"] != reg["file_sha256"]

    # Catalog now reports encrypted.
    got = await handle_get_file(session, {"id": reg["file_id"]})
    assert got["encrypted"] is True
    assert got["encrypted_algorithm"] == AES_256_GCM

    # reverify still passes against the freshly-persisted hashes.
    rev = await handle_reverify(session, {"id": reg["file_id"]})
    assert rev["drift"] is False

    # get_spectrum works if we supply the key_id.
    spec = await handle_get_spec(
        session,
        {
            "file_id": reg["file_id"],
            "run_name": "run_0001",
            "spectrum_index": 0,
            "key_id": key_id,
        },
        keyring=kr,
    )
    assert len(spec["channels"]["intensity"]) > 0

    # get_spectrum without key_id raises KeyRequired.
    with pytest.raises(KeyRequired):
        await handle_get_spec(
            session,
            {
                "file_id": reg["file_id"],
                "run_name": "run_0001",
                "spectrum_index": 0,
            },
            keyring=kr,
        )

    # Decrypt → back to plaintext.
    dec = await df.handle(
        session,
        {"id": reg["file_id"], "key_id": key_id},
        keyring=kr,
    )
    assert dec["encrypted"] is False
    assert dec["encrypted_algorithm"] is None

    got = await handle_get_file(session, {"id": reg["file_id"]})
    assert got["encrypted"] is False
    assert got["encrypted_algorithm"] is None

    rev = await handle_reverify(session, {"id": reg["file_id"]})
    assert rev["drift"] is False

    # get_spectrum works again without key_id.
    spec = await handle_get_spec(
        session,
        {
            "file_id": reg["file_id"],
            "run_name": "run_0001",
            "spectrum_index": 0,
        },
        keyring=kr,
    )
    assert len(spec["channels"]["intensity"]) > 0


async def test_encrypt_already_encrypted(
    session, ms_file: Path, keyring_with_key: tuple[Keyring, str, bytes]
) -> None:
    kr, key_id, _ = keyring_with_key
    reg = await handle_register(session, {"uri": str(ms_file)})
    await ef.handle(
        session, {"id": reg["file_id"], "key_id": key_id}, keyring=kr
    )
    with pytest.raises(ef.AlreadyEncrypted):
        await ef.handle(
            session, {"id": reg["file_id"], "key_id": key_id}, keyring=kr
        )


async def test_decrypt_not_encrypted(
    session, ms_file: Path, keyring_with_key: tuple[Keyring, str, bytes]
) -> None:
    kr, key_id, _ = keyring_with_key
    reg = await handle_register(session, {"uri": str(ms_file)})
    with pytest.raises(df.NotEncrypted):
        await df.handle(
            session, {"id": reg["file_id"], "key_id": key_id}, keyring=kr
        )


async def test_encrypt_remote_rejected(
    session, keyring_with_key: tuple[Keyring, str, bytes]
) -> None:
    kr, key_id, _ = keyring_with_key
    # Hand-stuff a remote row directly — register won't let us do it without
    # the cloud deps configured. Use the raw ORM to keep the test hermetic.
    from datetime import UTC, datetime

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

    with pytest.raises(ef.RemoteNotSupported):
        await ef.handle(
            session, {"id": row.id, "key_id": key_id}, keyring=kr
        )


async def test_decrypt_remote_rejected(
    session, keyring_with_key: tuple[Keyring, str, bytes]
) -> None:
    kr, key_id, _ = keyring_with_key
    from datetime import UTC, datetime

    from ttio_mcp.db.models import File

    row = File(
        uri="s3://fake-bucket/fake.mpgo",
        display_name="fake.mpgo",
        file_sha256="a" * 64,
        content_sha256="b" * 64,
        format_version="1.3",
        features={},
        encrypted=True,
        encrypted_algorithm=AES_256_GCM,
        signed=False,
        registered_by=1,
        owner_user_id=1,
        registered_at=datetime.now(UTC),
    )
    session.add(row)
    session.commit()

    with pytest.raises(df.RemoteNotSupported):
        await df.handle(
            session, {"id": row.id, "key_id": key_id}, keyring=kr
        )


async def test_get_spectrum_with_wrong_key_fails(
    session, tmp_path: Path, ms_file: Path
) -> None:
    # Two keys: encrypt with 'good', try to read with 'bad'.
    good = os.urandom(AES_256_GCM_KEY_LEN)
    bad = os.urandom(AES_256_GCM_KEY_LEN)
    path = tmp_path / "kr.json"
    path.write_text(
        json.dumps(
            {
                "keys": {
                    "good": {
                        "value": base64.b64encode(good).decode("ascii"),
                        "algorithm": AES_256_GCM,
                    },
                    "bad": {
                        "value": base64.b64encode(bad).decode("ascii"),
                        "algorithm": AES_256_GCM,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    kr = Keyring.from_path(path)
    reg = await handle_register(session, {"uri": str(ms_file)})
    await ef.handle(
        session, {"id": reg["file_id"], "key_id": "good"}, keyring=kr
    )

    from ttio_mcp.tools.get_spectrum import ReadFailed

    with pytest.raises(ReadFailed):
        await handle_get_spec(
            session,
            {
                "file_id": reg["file_id"],
                "run_name": "run_0001",
                "spectrum_index": 0,
                "key_id": "bad",
            },
            keyring=kr,
        )
