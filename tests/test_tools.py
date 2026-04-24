"""M2: tool handlers — the thin layer atop the catalog."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mpeg_o_mcp.tools.get_file import handle as handle_get
from mpeg_o_mcp.tools.list_files import handle as handle_list
from mpeg_o_mcp.tools.register import handle as handle_register
from mpeg_o_mcp.tools.reverify import handle as handle_reverify
from tests._fixtures import build_ms_fixture, build_nmr_fixture


@pytest.fixture
def ms_file(tmp_path: Path) -> Path:
    return build_ms_fixture(tmp_path / "ms.mpgo")


@pytest.fixture
def nmr_file(tmp_path: Path) -> Path:
    return build_nmr_fixture(tmp_path / "nmr.mpgo")


async def test_register_and_get(session, ms_file: Path) -> None:
    reg = await handle_register(session, {"uri": str(ms_file), "display_name": "Sample A"})
    assert reg["was_update"] is False
    assert reg["counts"]["runs"] == 1

    got = await handle_get(session, {"id": reg["file_id"]})
    assert got["id"] == reg["file_id"]
    assert got["display_name"] == "Sample A"
    assert got["counts"]["identifications"] == 2
    assert len(got["runs"]) == 1
    assert got["runs"][0]["acquisition_mode"] == "MS1_DDA"


async def test_list_with_filters(session, ms_file: Path, nmr_file: Path) -> None:
    await handle_register(session, {"uri": str(ms_file)})
    await handle_register(session, {"uri": str(nmr_file)})

    listing = await handle_list(session, {})
    assert listing["total"] == 2
    assert len(listing["files"]) == 2

    only_ms = await handle_list(session, {"acquisition_mode": "MS1_DDA"})
    assert only_ms["total"] == 1
    assert only_ms["files"][0]["format_version"] == "1.1"

    only_nmr = await handle_list(session, {"title_contains": "demo-nmr"})
    assert only_nmr["total"] == 1


async def test_list_pagination(session, tmp_path: Path) -> None:
    for i in range(3):
        build_ms_fixture(tmp_path / f"s{i}.mpgo", title=f"t-{i}")
        await handle_register(session, {"uri": str(tmp_path / f"s{i}.mpgo")})

    page1 = await handle_list(session, {"limit": 2, "offset": 0})
    page2 = await handle_list(session, {"limit": 2, "offset": 2})
    assert page1["total"] == 3
    assert len(page1["files"]) == 2
    assert len(page2["files"]) == 1
    assert {f["id"] for f in page1["files"]}.isdisjoint({f["id"] for f in page2["files"]})


async def test_reverify_no_drift(session, ms_file: Path) -> None:
    reg = await handle_register(session, {"uri": str(ms_file)})
    result = await handle_reverify(session, {"id": reg["file_id"]})
    assert result["resolved"] is True
    assert result["drift"] is False
    assert result["file_sha256"] == reg["file_sha256"]


async def test_reverify_with_drift(session, tmp_path: Path) -> None:
    p = build_ms_fixture(tmp_path / "s.mpgo")
    reg = await handle_register(session, {"uri": str(p)})

    # Rebuild with different content — same path, different bytes.
    p.unlink()
    build_ms_fixture(p, title="mutated", n_spectra=7)

    result = await handle_reverify(session, {"id": reg["file_id"]})
    assert result["drift"] is True
    assert result["file_sha256"] != reg["file_sha256"]


async def test_reverify_missing_file(session, tmp_path: Path) -> None:
    p = build_ms_fixture(tmp_path / "gone.mpgo")
    reg = await handle_register(session, {"uri": str(p)})
    p.unlink()

    result = await handle_reverify(session, {"id": reg["file_id"]})
    assert result["resolved"] is False
    assert "does not exist" in result["error"]


async def test_register_error_surfaces_as_catalog_error(session, tmp_path: Path) -> None:
    # Path that doesn't exist → ResolveFailed (a CatalogError subclass).
    from mpeg_o_mcp.catalog import ResolveFailed

    with pytest.raises(ResolveFailed):
        await handle_register(session, {"uri": str(tmp_path / "nope.mpgo")})


async def test_get_file_unknown_id_raises(session) -> None:
    from mpeg_o_mcp.catalog import NotFound

    with pytest.raises(NotFound):
        await handle_get(session, {"id": 42})


def test_tool_schemas_are_valid_json() -> None:
    # The registration module is the public tool surface — make sure
    # each schema round-trips through json, since MCP serializes them.
    from mpeg_o_mcp.tools import TOOLS

    for name, desc, schema, _handler in TOOLS:
        assert isinstance(name, str) and name.startswith("mpgo_")
        assert desc
        round_trip = json.loads(json.dumps(schema))
        assert round_trip == schema
        assert round_trip["type"] == "object"
