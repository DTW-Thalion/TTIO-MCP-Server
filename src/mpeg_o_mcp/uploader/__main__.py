"""Entry point for ``python -m mpeg_o_mcp.uploader``.

The server spawns this module as a child process, reads a single JSON
line from its stdout, and returns the payload to the MCP caller. Exit
codes:

- ``0`` — user picked a file and it was copied into the intake dir.
- ``2`` — user cancelled the picker.
- ``3`` — configuration error (``MPGO_MCP_INTAKE_DIR`` unset, etc.).
- ``4`` — runtime error during the copy.

Exit code is a *hint*; the authoritative result is the JSON body on
stdout. The server inspects ``ok`` / ``error.code`` rather than the
exit code.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from mpeg_o_mcp.uploader.core import (
    detect_format,
    get_intake_dir,
)


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def main() -> int:
    intake_dir = get_intake_dir()
    if intake_dir is None:
        _emit(
            _error(
                "intake_not_configured",
                "MPGO_MCP_INTAKE_DIR is not set on the server process.",
            )
        )
        return 3

    try:
        from mpeg_o_mcp.uploader.gui import (
            copy_to_intake_with_progress,
            pick_file,
        )
    except Exception as exc:  # pragma: no cover — tkinter missing entirely
        _emit(_error("no_display", f"tkinter import failed: {exc}"))
        return 3

    try:
        source = pick_file()
    except RuntimeError as exc:
        _emit(_error("no_display", str(exc)))
        return 3
    except Exception as exc:  # pragma: no cover
        _emit(_error("upload_failed", f"file picker crashed: {exc}"))
        return 4

    if source is None:
        _emit(_error("cancelled", "User cancelled the file picker."))
        return 2

    try:
        destination = copy_to_intake_with_progress(source, intake_dir)
    except RuntimeError as exc:
        _emit(_error("no_display", str(exc)))
        return 3
    except Exception as exc:
        _emit(
            _error(
                "upload_failed",
                f"failed to copy {source} into {intake_dir}: {exc}",
            )
        )
        traceback.print_exc(file=sys.stderr)
        return 4

    fmt = detect_format(Path(destination))
    _emit(
        {
            "ok": True,
            "data": {
                "source": str(source),
                "destination": str(destination),
                "format": fmt,
                "size_bytes": destination.stat().st_size,
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
