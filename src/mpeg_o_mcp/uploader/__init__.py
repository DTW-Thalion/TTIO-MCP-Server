"""Server-spawned local uploader GUI.

The MPEG-O MCP server runs as a same-machine stdio subprocess of the
MCP client. That means we can spawn *another* local subprocess — a
tiny tkinter file-picker — so the human user can stage a binary file
(mzML / nmrML / imzML / mzTab / .mpgo) without having to paste
content through the chat or prearrange a URI.

The uploader:

- Reads ``MPGO_MCP_INTAKE_DIR`` from the environment.
- Opens a file picker.
- Copies the chosen file into the intake dir (with a timestamped
  suffix if the destination exists).
- Writes a single-line JSON result to stdout and exits.

The server's ``mpgo_launch_uploader`` tool handler ``Popen``s this
module, captures stdout, and returns the JSON payload to the caller.
"""

from mpeg_o_mcp.uploader.core import (
    IMPORTABLE_EXTENSIONS,
    copy_to_intake,
    detect_format,
    get_intake_dir,
)

__all__ = [
    "IMPORTABLE_EXTENSIONS",
    "copy_to_intake",
    "detect_format",
    "get_intake_dir",
]
