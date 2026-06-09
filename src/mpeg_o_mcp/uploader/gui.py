"""tkinter front-ends for the uploader subprocess.

Two widgets live here:

- :func:`pick_file` — modal open-file dialog.
- :func:`copy_to_intake_with_progress` — determinate progress window
  that drives :func:`mpeg_o_mcp.uploader.core.copy_to_intake` from a
  worker thread.

Both lazily import tkinter so the rest of the uploader (pure-logic
``core``, JSON-emitting ``__main__``, server-side handler) can be
imported and unit-tested on a host without a display server.
"""
from __future__ import annotations

import queue
import threading
from datetime import datetime
from pathlib import Path

from mpeg_o_mcp.uploader.core import IMPORTABLE_EXTENSIONS, copy_to_intake


def pick_file(title: str = "Choose a file to upload to MPEG-O") -> Path | None:
    """Show a modal file-picker; return the chosen path or ``None``.

    ``None`` means the user cancelled. Raises ``RuntimeError`` wrapping
    the underlying ``TclError`` when no display is available — the
    ``__main__`` entry point turns that into a ``no_display`` JSON
    error so the server can surface it cleanly.
    """
    # Imported lazily so "no display" failures surface from pick_file,
    # not at module import time (which would break pytest collection).
    import tkinter as tk
    from tkinter import TclError, filedialog

    try:
        root = tk.Tk()
    except TclError as exc:
        raise RuntimeError(f"no display available for tkinter: {exc}") from exc

    root.withdraw()
    try:
        filetypes = [
            ("MPEG-O importable", " ".join(f"*{ext}" for ext in IMPORTABLE_EXTENSIONS)),
            *[(fmt.upper(), f"*{ext}") for ext, fmt in IMPORTABLE_EXTENSIONS.items()],
            ("All files", "*.*"),
        ]
        selected = filedialog.askopenfilename(title=title, filetypes=filetypes)
    finally:
        root.destroy()

    if not selected:
        return None
    return Path(selected)


def copy_to_intake_with_progress(
    source: Path,
    intake_dir: Path,
    *,
    overwrite: bool = False,
    now: datetime | None = None,
    poll_ms: int = 50,
    hold_ms: int = 300,
) -> Path:
    """Copy *source* into *intake_dir* with a tkinter progress window.

    Delegates to :func:`copy_to_intake` on a daemon worker thread and
    pumps a ``ttk.Progressbar`` from the main thread. The worker posts
    ``(copied, total)`` tuples onto a ``queue.Queue``; the UI pump
    reads them via ``root.after(poll_ms, …)`` so tkinter stays on its
    own thread.

    ``hold_ms`` keeps the "100% — done" window visible briefly so the
    user sees completion before it disappears (otherwise the window
    vanishes faster than the eye can read on small files).

    Raises the same exceptions as :func:`copy_to_intake` — a failed
    copy propagates out after the UI is torn down.
    """
    import tkinter as tk
    from tkinter import TclError, ttk

    try:
        root = tk.Tk()
    except TclError as exc:
        raise RuntimeError(f"no display available for tkinter: {exc}") from exc

    root.title("MPEG-O uploader")
    root.resizable(False, False)

    tk.Label(
        root,
        text=f"Copying {source.name} into intake…",
        anchor="w",
    ).pack(fill="x", padx=16, pady=(12, 4))

    bar = ttk.Progressbar(
        root, length=420, mode="determinate", maximum=100.0, value=0.0
    )
    bar.pack(padx=16, pady=4)

    status_var = tk.StringVar(value="0.0% — 0.00 / 0.00 MiB")
    tk.Label(root, textvariable=status_var, anchor="w").pack(
        fill="x", padx=16, pady=(4, 12)
    )

    # Worker → UI channel. Messages are ("progress", copied, total),
    # ("done", destination), or ("error", exception).
    q: queue.Queue[tuple] = queue.Queue()

    def progress_cb(copied: int, total: int) -> None:
        q.put(("progress", copied, total))

    def worker() -> None:
        try:
            dest = copy_to_intake(
                source,
                intake_dir,
                overwrite=overwrite,
                now=now,
                progress=progress_cb,
            )
        except BaseException as exc:  # noqa: BLE001 — re-raised on main thread
            q.put(("error", exc))
            return
        q.put(("done", dest))

    state: dict = {"result": None, "error": None, "finished": False}

    def pump() -> None:
        drained_any = False
        try:
            while True:
                msg = q.get_nowait()
                drained_any = True
                kind = msg[0]
                if kind == "progress":
                    _, copied, total = msg
                    pct = 100.0 if total == 0 else (copied / total) * 100.0
                    bar["value"] = pct
                    status_var.set(
                        f"{pct:.1f}% — "
                        f"{copied / (1024 * 1024):.2f} "
                        f"/ {total / (1024 * 1024):.2f} MiB"
                    )
                elif kind == "done":
                    state["result"] = msg[1]
                    state["finished"] = True
                    bar["value"] = 100.0
                elif kind == "error":
                    state["error"] = msg[1]
                    state["finished"] = True
        except queue.Empty:
            pass

        if state["finished"]:
            # Hold the 100% frame briefly so the user registers completion.
            root.after(hold_ms, root.destroy)
        else:
            # Drain aggressively when activity is high, back off when idle.
            root.after(poll_ms if not drained_any else 10, pump)

    threading.Thread(target=worker, daemon=True).start()
    root.after(poll_ms, pump)
    root.mainloop()

    if state["error"] is not None:
        raise state["error"]
    assert state["result"] is not None, "worker never posted a result"
    return state["result"]
