"""Shallow-merge per-call fsspec kwargs on top of the env default.

``MPGO_MCP_FSSPEC_KWARGS`` (parsed once per call by
:meth:`Config.from_env`) supplies the baseline kwargs every cloud URI
inherits. Per-call ``fsspec_kwargs`` on ``mpgo_register_file`` /
``mpgo_get_spectrum`` override individual keys but do not clobber the
whole dict.
"""
from __future__ import annotations

from typing import Any

from mpeg_o_mcp.config import Config


def merged_fsspec_kwargs(per_call: dict[str, Any] | None) -> dict[str, Any]:
    base = Config.from_env().fsspec_kwargs
    if not per_call:
        return dict(base)
    merged = dict(base)
    merged.update(per_call)
    return merged
