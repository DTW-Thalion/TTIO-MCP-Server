"""The MCP server frames JSON-RPC on stdout, so stray stdout writes must not
reach it. _reserve_stdout_for_protocol reserves fd 1 for the protocol and sends
everything else to stderr. Verified in a subprocess so it never touches pytest's
own stdout capture.
"""
import subprocess
import sys
import textwrap


def test_stray_stdout_redirected_to_stderr():
    code = textwrap.dedent(
        """
        import os
        from ttio_mcp.server import _reserve_stdout_for_protocol

        protocol = _reserve_stdout_for_protocol()
        os.write(1, b"STRAY_FD1\\n")   # C-level write to fd 1 -> must go to stderr
        print("PYPRINT")              # sys.stdout -> fd 1 -> must go to stderr
        protocol.write("PROTOCOL_FRAME\\n")
        protocol.flush()              # the protocol stream -> real stdout
        """
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    # Only protocol output reaches stdout.
    assert r.stdout.strip() == "PROTOCOL_FRAME"
    assert "STRAY_FD1" not in r.stdout
    assert "PYPRINT" not in r.stdout
    # The stray writes are preserved on stderr (not lost).
    assert "STRAY_FD1" in r.stderr
    assert "PYPRINT" in r.stderr
