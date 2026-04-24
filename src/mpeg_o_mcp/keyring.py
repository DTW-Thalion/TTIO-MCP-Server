"""JSON-file-backed keyring for AES-256-GCM encryption keys.

The keyring is a flat JSON file whose path is controlled by
``MPGO_KEYRING_PATH``. Keys are stored as base64-encoded bytes alongside
metadata (``algorithm``, ``created_at``, optional ``description``).

Listing the keyring returns metadata only — the key value is never
exposed through MCP tool responses. Tool calls reference keys by
``key_id`` (the map key in the JSON file); the keyring resolves
``key_id`` → raw bytes server-side.

File layout::

    {
      "keys": {
        "demo": {
          "value": "base64-encoded 32 bytes",
          "algorithm": "AES-256-GCM",
          "created_at": "2026-04-24T12:00:00+00:00",
          "description": "optional"
        }
      }
    }

A missing file is a valid empty keyring — any key lookup raises
:class:`KeyNotFound`. The file is loaded lazily on first access and
cached; call :meth:`Keyring.reload` to pick up on-disk changes.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

AES_256_GCM = "AES-256-GCM"
AES_256_GCM_KEY_LEN = 32


class KeyringError(Exception):
    """Base class for keyring errors that should surface as tool errors."""

    code = "keyring_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class KeyringNotConfigured(KeyringError):
    code = "keyring_not_configured"


class KeyNotFound(KeyringError):
    code = "key_not_found"


class InvalidKeyring(KeyringError):
    code = "invalid_keyring"


@dataclass(frozen=True)
class KeyEntry:
    """Public, secret-free view of a keyring entry."""

    key_id: str
    algorithm: str
    created_at: str | None
    description: str | None


class Keyring:
    """JSON-file-backed keyring.

    Instantiate via :meth:`from_env` or :meth:`from_path`. The keyring
    is read lazily; ``get``/``list_entries`` trigger the first load.
    """

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._loaded = False
        self._entries: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_env(cls) -> Keyring:
        raw = os.environ.get("MPGO_KEYRING_PATH", "").strip()
        if not raw:
            return cls(None)
        return cls(Path(raw).expanduser())

    @classmethod
    def from_path(cls, path: str | Path | None) -> Keyring:
        if path is None:
            return cls(None)
        return cls(Path(path).expanduser())

    @property
    def path(self) -> Path | None:
        return self._path

    def reload(self) -> None:
        self._loaded = False
        self._entries = {}

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if self._path is None:
            self._loaded = True
            return
        if not self._path.exists():
            self._loaded = True
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            doc = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidKeyring(
                f"cannot read keyring at {self._path}: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(doc, dict) or "keys" not in doc:
            raise InvalidKeyring(
                f"keyring file {self._path} must be a JSON object with a 'keys' key"
            )
        keys = doc["keys"]
        if not isinstance(keys, dict):
            raise InvalidKeyring(
                f"keyring 'keys' must be a JSON object (got {type(keys).__name__})"
            )
        for key_id, entry in keys.items():
            if not isinstance(entry, dict) or "value" not in entry:
                raise InvalidKeyring(
                    f"keyring entry {key_id!r} must be a JSON object with 'value'"
                )
        self._entries = keys
        self._loaded = True

    def get(self, key_id: str) -> bytes:
        """Resolve ``key_id`` to raw key bytes.

        Raises :class:`KeyringNotConfigured` if no ``MPGO_KEYRING_PATH``
        is set, :class:`KeyNotFound` if the id is absent, and
        :class:`InvalidKeyring` for malformed entries.
        """
        if self._path is None:
            raise KeyringNotConfigured(
                "no keyring configured; set MPGO_KEYRING_PATH"
            )
        self._ensure_loaded()
        entry = self._entries.get(key_id)
        if entry is None:
            raise KeyNotFound(f"no key with id {key_id!r} in keyring")
        value = entry.get("value")
        if not isinstance(value, str):
            raise InvalidKeyring(
                f"key {key_id!r}: 'value' must be a base64 string"
            )
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise InvalidKeyring(
                f"key {key_id!r}: value is not valid base64: {exc}"
            ) from exc
        algorithm = entry.get("algorithm", AES_256_GCM)
        if algorithm != AES_256_GCM:
            raise InvalidKeyring(
                f"key {key_id!r}: unsupported algorithm {algorithm!r} "
                f"(only {AES_256_GCM} is supported)"
            )
        if len(raw) != AES_256_GCM_KEY_LEN:
            raise InvalidKeyring(
                f"key {key_id!r}: expected {AES_256_GCM_KEY_LEN}-byte key, "
                f"got {len(raw)} bytes"
            )
        return raw

    def list_entries(self) -> list[KeyEntry]:
        """Return metadata-only view of all keys (no secret bytes)."""
        if self._path is None:
            return []
        self._ensure_loaded()
        out: list[KeyEntry] = []
        for key_id, entry in self._entries.items():
            out.append(
                KeyEntry(
                    key_id=key_id,
                    algorithm=entry.get("algorithm", AES_256_GCM),
                    created_at=entry.get("created_at"),
                    description=entry.get("description"),
                )
            )
        return out

    def algorithm_for(self, key_id: str) -> str:
        """Return the algorithm tag for ``key_id`` without reading the bytes."""
        if self._path is None:
            raise KeyringNotConfigured(
                "no keyring configured; set MPGO_KEYRING_PATH"
            )
        self._ensure_loaded()
        entry = self._entries.get(key_id)
        if entry is None:
            raise KeyNotFound(f"no key with id {key_id!r} in keyring")
        return entry.get("algorithm", AES_256_GCM)
