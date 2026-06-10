# src/ttio_mcp/tools/_serialize.py
"""Shared serialization helper for tool results."""
from __future__ import annotations

import dataclasses
from typing import Any


def ser(obj: Any) -> Any:
    """Recursively convert dataclass instances (and lists/tuples of them) to
    plain dicts/lists for JSON-serializable tool output. Scalars pass through.

    Note: assumes dataclass fields are already JSON-friendly (the workbench SDK
    builds these dataclasses from JSON responses, so fields are str/int/bool/None
    or nested dataclasses)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: ser(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [ser(x) for x in obj]
    return obj
