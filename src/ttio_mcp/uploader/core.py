"""Pure-logic core for the uploader — no GUI, no subprocess.

Isolated from :mod:`ttio_mcp.uploader.gui` so the bulk of the
uploader's behaviour is unit-testable without a display server.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

IMPORTABLE_EXTENSIONS: dict[str, str] = {
    ".mpgo": "mpgo",
    ".mzml": "mzml",
    ".nmrml": "nmrml",
    ".imzml": "imzml",
    ".mztab": "mztab",
}

DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB — balances syscall overhead vs. UI updates.

ProgressCallback = Callable[[int, int], None]
"""``(bytes_copied, total_bytes)``. Invoked once per chunk and once at EOF."""


def detect_format(path: Path) -> str | None:
    """Return the TTI-O format identifier for *path*, or ``None``.

    Detection is purely suffix-based (case-insensitive). The server's
    import tool is expected to re-validate by opening the file, so this
    is only a hint for the uploader's JSON payload and for filtering
    the tkinter file dialog.
    """
    return IMPORTABLE_EXTENSIONS.get(path.suffix.lower())


def get_intake_dir() -> Path | None:
    """Resolve ``TTIO_MCP_INTAKE_DIR`` to an absolute :class:`Path`.

    Returns ``None`` when the variable is unset or empty. The directory
    is **not** created here — the caller decides whether to auto-create
    or surface ``intake_not_configured``.
    """
    raw = os.environ.get("TTIO_MCP_INTAKE_DIR", "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def copy_to_intake(
    source: Path,
    intake_dir: Path,
    *,
    overwrite: bool = False,
    now: datetime | None = None,
    progress: ProgressCallback | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Path:
    """Copy *source* into *intake_dir*, returning the destination path.

    If a file already exists at the natural destination and
    ``overwrite`` is false, a UTC timestamp is inserted before the
    extension (``sample.mpgo`` → ``sample-20260424T120000Z.mpgo``). If
    the timestamped path *also* exists, an integer counter is appended.

    ``progress``, when supplied, is invoked as ``(copied, total)``
    after each chunk plus once with ``(total, total)`` on completion
    — so GUI callers can safely latch 100% without a separate end
    signal. The callback runs on the calling thread; the tkinter
    front-end marshals onto the UI thread itself.

    A partial destination file is removed if the copy fails mid-stream
    so a retry doesn't trip the collision branch.

    ``now`` is injectable to keep tests deterministic.
    """
    if not source.is_file():
        raise FileNotFoundError(f"source is not a file: {source}")

    intake_dir.mkdir(parents=True, exist_ok=True)

    natural = intake_dir / source.name
    if overwrite or not natural.exists():
        _copy_with_progress(source, natural, progress, chunk_size)
        return natural

    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    stem = source.stem
    suffix = source.suffix
    candidate = intake_dir / f"{stem}-{stamp}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = intake_dir / f"{stem}-{stamp}-{counter}{suffix}"
        counter += 1

    _copy_with_progress(source, candidate, progress, chunk_size)
    return candidate


def _copy_with_progress(
    src: Path,
    dst: Path,
    progress: ProgressCallback | None,
    chunk_size: int,
) -> None:
    total = src.stat().st_size
    copied = 0
    try:
        with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
            while True:
                chunk = fsrc.read(chunk_size)
                if not chunk:
                    break
                fdst.write(chunk)
                copied += len(chunk)
                if progress is not None:
                    progress(copied, total)
        shutil.copystat(src, dst)
    except BaseException:
        # Remove the partial destination so a retry hits a clean slate
        # rather than the collision branch.
        try:
            dst.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    if progress is not None:
        progress(total, total)
