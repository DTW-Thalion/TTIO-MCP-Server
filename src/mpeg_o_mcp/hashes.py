"""Checksum helpers for the catalog.

``file_sha256`` streams the whole-file bytes. ``content_sha256`` is an
alias for ``file_sha256`` in M2; a semantic-content hash (stable under
timestamp / signature-attr churn) lands in a later milestone. The
database column is kept distinct for forward compatibility.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1 << 20  # 1 MiB


def hash_file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            buf = f.read(_CHUNK)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def hash_content_sha256(path: str | Path) -> str:
    return hash_file_sha256(path)
