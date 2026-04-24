"""Checksum helpers for the catalog.

``file_sha256`` streams the whole-file bytes. For remote URIs (s3://,
https://, ...) we stream through ``mpeg_o.remote.open_remote_file`` —
note that this pulls the full object across the wire, so registering a
multi-GB cloud file is bandwidth-bound.

``content_sha256`` is an alias for ``file_sha256`` in M2+; a
semantic-content hash (stable under timestamp / signature-attr churn)
lands in a later milestone. The database column is kept distinct for
forward compatibility.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

_CHUNK = 1 << 20  # 1 MiB


def hash_file_sha256(
    target: str | Path,
    *,
    fsspec_kwargs: dict[str, Any] | None = None,
) -> str:
    """Hash a local path or remote URI. Remote URIs pull the full object."""
    from mpeg_o.remote import is_remote_url

    if isinstance(target, str) and is_remote_url(target):
        return _hash_stream_from_remote(target, fsspec_kwargs or {})

    h = hashlib.sha256()
    with Path(target).open("rb") as f:
        while True:
            buf = f.read(_CHUNK)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _hash_stream_from_remote(url: str, fsspec_kwargs: dict[str, Any]) -> str:
    from mpeg_o.remote import open_remote_file

    h = hashlib.sha256()
    fh = open_remote_file(url, **fsspec_kwargs)
    try:
        while True:
            buf = fh.read(_CHUNK)
            if not buf:
                break
            h.update(buf)
    finally:
        fh.close()
    return h.hexdigest()


def hash_content_sha256(
    target: str | Path,
    *,
    fsspec_kwargs: dict[str, Any] | None = None,
) -> str:
    return hash_file_sha256(target, fsspec_kwargs=fsspec_kwargs)
