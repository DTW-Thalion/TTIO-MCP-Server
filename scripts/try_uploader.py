"""Manual harness for the uploader subprocess.

Run directly — this is NOT a pytest test (it pops a real tkinter
window). Use it to confirm the end-to-end behaviour on a machine with
a display.

    python scripts/try_uploader.py [--intake-dir DIR]

Without --intake-dir, a fresh temp directory is created and printed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--intake-dir",
        type=Path,
        default=None,
        help="Directory to copy the chosen file into. Defaults to a temp dir.",
    )
    args = parser.parse_args()

    if args.intake_dir is None:
        intake = Path(tempfile.mkdtemp(prefix="mpgo-intake-"))
    else:
        intake = args.intake_dir.expanduser().resolve()
        intake.mkdir(parents=True, exist_ok=True)

    print(f"intake dir: {intake}", file=sys.stderr)

    env = os.environ.copy()
    env["TTIO_MCP_INTAKE_DIR"] = str(intake)

    proc = subprocess.run(
        [sys.executable, "-m", "ttio_mcp.uploader"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    print(f"exit code: {proc.returncode}", file=sys.stderr)
    if proc.stderr:
        print(f"stderr: {proc.stderr}", file=sys.stderr)

    stdout = (proc.stdout or "").strip()
    if not stdout:
        print("(no stdout)", file=sys.stderr)
        return proc.returncode or 1

    try:
        payload = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        print(f"raw stdout:\n{stdout}")
        return 1

    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
