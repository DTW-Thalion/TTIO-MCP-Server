"""M5: Keyring loader — file format, validation, error surface."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from mpeg_o_mcp.keyring import (
    AES_256_GCM,
    AES_256_GCM_KEY_LEN,
    InvalidKeyring,
    KeyNotFound,
    Keyring,
    KeyringNotConfigured,
)


def _write_keyring(path: Path, entries: dict[str, dict]) -> Path:
    path.write_text(json.dumps({"keys": entries}), encoding="utf-8")
    return path


def _b64_key(nbytes: int = AES_256_GCM_KEY_LEN) -> str:
    return base64.b64encode(b"\x11" * nbytes).decode("ascii")


def test_from_path_none_is_unconfigured() -> None:
    kr = Keyring.from_path(None)
    assert kr.path is None
    with pytest.raises(KeyringNotConfigured):
        kr.get("anything")
    # list_entries on unconfigured returns empty rather than raising.
    assert kr.list_entries() == []


def test_from_env_reads_mpgo_keyring_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    p = _write_keyring(tmp_path / "kr.json", {})
    monkeypatch.setenv("MPGO_KEYRING_PATH", str(p))
    kr = Keyring.from_env()
    assert kr.path == p


def test_from_env_blank_is_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MPGO_KEYRING_PATH", "   ")
    kr = Keyring.from_env()
    assert kr.path is None


def test_missing_file_is_empty_keyring(tmp_path: Path) -> None:
    kr = Keyring.from_path(tmp_path / "does_not_exist.json")
    # Missing file is valid — just no keys.
    assert kr.list_entries() == []
    with pytest.raises(KeyNotFound):
        kr.get("demo")


def test_get_returns_raw_bytes(tmp_path: Path) -> None:
    raw = b"\xab" * AES_256_GCM_KEY_LEN
    p = _write_keyring(
        tmp_path / "kr.json",
        {
            "demo": {
                "value": base64.b64encode(raw).decode("ascii"),
                "algorithm": AES_256_GCM,
                "created_at": "2026-04-24T00:00:00+00:00",
                "description": "test key",
            }
        },
    )
    kr = Keyring.from_path(p)
    assert kr.get("demo") == raw
    entries = kr.list_entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.key_id == "demo"
    assert entry.algorithm == AES_256_GCM
    assert entry.description == "test key"


def test_list_entries_never_includes_value(tmp_path: Path) -> None:
    p = _write_keyring(
        tmp_path / "kr.json",
        {"demo": {"value": _b64_key(), "algorithm": AES_256_GCM}},
    )
    kr = Keyring.from_path(p)
    entries = kr.list_entries()
    # KeyEntry has no 'value' attribute by design.
    assert not hasattr(entries[0], "value")


def test_invalid_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "kr.json"
    p.write_text("{ not-json", encoding="utf-8")
    kr = Keyring.from_path(p)
    with pytest.raises(InvalidKeyring):
        kr.get("demo")


def test_missing_keys_top_level_raises(tmp_path: Path) -> None:
    p = tmp_path / "kr.json"
    p.write_text(json.dumps({"not_keys": {}}), encoding="utf-8")
    kr = Keyring.from_path(p)
    with pytest.raises(InvalidKeyring):
        kr.get("demo")


def test_entry_without_value_raises(tmp_path: Path) -> None:
    p = _write_keyring(
        tmp_path / "kr.json", {"demo": {"algorithm": AES_256_GCM}}
    )
    kr = Keyring.from_path(p)
    with pytest.raises(InvalidKeyring):
        kr.get("demo")


def test_invalid_base64_raises(tmp_path: Path) -> None:
    p = _write_keyring(
        tmp_path / "kr.json",
        {"demo": {"value": "!!!not-base64!!!", "algorithm": AES_256_GCM}},
    )
    kr = Keyring.from_path(p)
    with pytest.raises(InvalidKeyring):
        kr.get("demo")


def test_wrong_algorithm_raises(tmp_path: Path) -> None:
    p = _write_keyring(
        tmp_path / "kr.json",
        {"demo": {"value": _b64_key(), "algorithm": "AES-128-CBC"}},
    )
    kr = Keyring.from_path(p)
    with pytest.raises(InvalidKeyring):
        kr.get("demo")


def test_wrong_key_length_raises(tmp_path: Path) -> None:
    p = _write_keyring(
        tmp_path / "kr.json",
        {"demo": {"value": _b64_key(nbytes=16), "algorithm": AES_256_GCM}},
    )
    kr = Keyring.from_path(p)
    with pytest.raises(InvalidKeyring):
        kr.get("demo")


def test_key_not_found(tmp_path: Path) -> None:
    p = _write_keyring(
        tmp_path / "kr.json",
        {"demo": {"value": _b64_key(), "algorithm": AES_256_GCM}},
    )
    kr = Keyring.from_path(p)
    with pytest.raises(KeyNotFound):
        kr.get("missing")


def test_algorithm_for_without_reading_value(tmp_path: Path) -> None:
    p = _write_keyring(
        tmp_path / "kr.json",
        {"demo": {"value": _b64_key(), "algorithm": AES_256_GCM}},
    )
    kr = Keyring.from_path(p)
    assert kr.algorithm_for("demo") == AES_256_GCM
    with pytest.raises(KeyNotFound):
        kr.algorithm_for("missing")


def test_reload_picks_up_file_changes(tmp_path: Path) -> None:
    p = _write_keyring(
        tmp_path / "kr.json",
        {"a": {"value": _b64_key(), "algorithm": AES_256_GCM}},
    )
    kr = Keyring.from_path(p)
    assert {e.key_id for e in kr.list_entries()} == {"a"}

    _write_keyring(
        p,
        {
            "a": {"value": _b64_key(), "algorithm": AES_256_GCM},
            "b": {"value": _b64_key(), "algorithm": AES_256_GCM},
        },
    )
    # Cached — still only sees 'a'.
    assert {e.key_id for e in kr.list_entries()} == {"a"}
    kr.reload()
    assert {e.key_id for e in kr.list_entries()} == {"a", "b"}
