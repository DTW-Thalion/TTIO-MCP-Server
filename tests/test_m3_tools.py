"""M3: query tool handlers — search, get_run, get_spectrum, get_quantifications."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._fixtures import build_ms_fixture, build_nmr_fixture
from ttio_mcp.catalog import NotFound
from ttio_mcp.tools.get_quantifications import handle as handle_get_quant
from ttio_mcp.tools.get_run import handle as handle_get_run
from ttio_mcp.tools.get_spectrum import InvalidArgument
from ttio_mcp.tools.get_spectrum import handle as handle_get_spec
from ttio_mcp.tools.register import handle as handle_register
from ttio_mcp.tools.search_identifications import handle as handle_search


@pytest.fixture
def ms_file(tmp_path: Path) -> Path:
    return build_ms_fixture(tmp_path / "ms.mpgo")


@pytest.fixture
def nmr_file(tmp_path: Path) -> Path:
    return build_nmr_fixture(tmp_path / "nmr.mpgo")


async def _register_both(session, ms_file: Path, nmr_file: Path) -> tuple[int, int]:
    ms = await handle_register(session, {"uri": str(ms_file)})
    nmr = await handle_register(session, {"uri": str(nmr_file)})
    return ms["file_id"], nmr["file_id"]


async def test_search_identifications_no_filters(
    session, ms_file: Path, nmr_file: Path
) -> None:
    await _register_both(session, ms_file, nmr_file)

    result = await handle_search(session, {})
    assert result["total"] == 2  # MS fixture has 2; NMR has 0
    # Sorted by score desc.
    scores = [i["score"] for i in result["identifications"]]
    assert scores == sorted(scores, reverse=True)
    assert result["identifications"][0]["file_uri"].startswith("file://")
    assert result["identifications"][0]["acquisition_mode"] == "MS1_DDA"


async def test_search_identifications_chebi_filter(session, ms_file: Path) -> None:
    await handle_register(session, {"uri": str(ms_file)})
    hit = await handle_search(session, {"chebi_id": "CHEBI:15377"})
    assert hit["total"] == 1
    assert hit["identifications"][0]["chebi_id"] == "CHEBI:15377"
    assert hit["identifications"][0]["evidence_chain"] == ["ev:peak"]

    miss = await handle_search(session, {"chebi_id": "CHEBI:99999"})
    assert miss["total"] == 0
    assert miss["identifications"] == []


async def test_search_identifications_min_score(session, ms_file: Path) -> None:
    await handle_register(session, {"uri": str(ms_file)})
    hi = await handle_search(session, {"min_score": 0.9})
    assert hi["total"] == 1
    assert hi["identifications"][0]["score"] == pytest.approx(0.95)


async def test_search_identifications_pagination(session, tmp_path: Path) -> None:
    # 3 files × 2 identifications each = 6 hits.
    for i in range(3):
        p = build_ms_fixture(tmp_path / f"f{i}.mpgo", title=f"t-{i}")
        await handle_register(session, {"uri": str(p)})

    page1 = await handle_search(session, {"limit": 4, "offset": 0})
    page2 = await handle_search(session, {"limit": 4, "offset": 4})
    assert page1["total"] == 6 == page2["total"]
    assert len(page1["identifications"]) == 4
    assert len(page2["identifications"]) == 2
    ids1 = {i["id"] for i in page1["identifications"]}
    ids2 = {i["id"] for i in page2["identifications"]}
    assert ids1.isdisjoint(ids2)


async def test_get_run_by_id(session, ms_file: Path) -> None:
    reg = await handle_register(session, {"uri": str(ms_file)})
    # The registration result has run count but not run_id; find it via search.
    search = await handle_search(session, {"file_id": reg["file_id"]})
    run_id = search["identifications"][0]["run_id"]

    detail = await handle_get_run(session, {"run_id": run_id})
    assert detail["id"] == run_id
    assert detail["name"] == "run_0001"
    assert detail["acquisition_mode"] == "MS1_DDA"
    assert detail["spectrum_count"] == 5
    assert detail["channel_names"] == ["mz", "intensity"]
    assert len(detail["identifications"]) == 2
    assert len(detail["quantifications"]) == 2


async def test_get_run_by_name(session, ms_file: Path) -> None:
    reg = await handle_register(session, {"uri": str(ms_file)})
    detail = await handle_get_run(
        session, {"file_id": reg["file_id"], "run_name": "run_0001"}
    )
    assert detail["name"] == "run_0001"


async def test_get_run_not_found(session, ms_file: Path) -> None:
    reg = await handle_register(session, {"uri": str(ms_file)})
    with pytest.raises(NotFound):
        await handle_get_run(
            session, {"file_id": reg["file_id"], "run_name": "no-such-run"}
        )


async def test_get_spectrum_ms(session, empty_keyring, ms_file: Path) -> None:
    reg = await handle_register(session, {"uri": str(ms_file)})
    search = await handle_search(session, {"file_id": reg["file_id"]})
    run_id = search["identifications"][0]["run_id"]

    spec = await handle_get_spec(
        session, {"run_id": run_id, "spectrum_index": 0}, keyring=empty_keyring
    )
    assert spec["run_id"] == run_id
    assert spec["spectrum_index"] == 0
    assert spec["spectrum_class"] == "MassSpectrum"
    assert set(spec["channels"].keys()) == {"mz", "intensity"}
    assert len(spec["channels"]["mz"]) == 8  # n_points in fixture
    assert spec["truncated"] is False
    assert spec["original_length"] == 8
    assert spec["returned_length"] == 8
    assert spec["metadata"]["ms_level"] == 1.0


async def test_get_spectrum_nmr(session, empty_keyring, nmr_file: Path) -> None:
    reg = await handle_register(session, {"uri": str(nmr_file)})
    # NMR has exactly one run.
    detail = await handle_get_run(
        session, {"file_id": reg["file_id"], "run_name": "nmr_run"}
    )
    spec = await handle_get_spec(
        session,
        {"run_id": detail["id"], "spectrum_index": 0},
        keyring=empty_keyring,
    )
    assert spec["spectrum_class"] == "NMRSpectrum"
    assert "chemical_shift" in spec["channels"]
    assert "intensity" in spec["channels"]


async def test_get_spectrum_downsampling(
    session, empty_keyring, tmp_path: Path
) -> None:
    # 64 points/spectrum; request max_points=10 → stride=7, returned=10.
    p = build_ms_fixture(tmp_path / "dense.mpgo", n_spectra=2, n_points=64)
    reg = await handle_register(session, {"uri": str(p)})
    search = await handle_search(session, {"file_id": reg["file_id"]})
    run_id = search["identifications"][0]["run_id"]

    spec = await handle_get_spec(
        session,
        {"run_id": run_id, "spectrum_index": 0, "max_points": 10},
        keyring=empty_keyring,
    )
    assert spec["truncated"] is True
    assert spec["original_length"] == 64
    # stride = ceil(64/10) = 7 → 10 elements (64 // 7 = 9 + remainder).
    assert spec["returned_length"] <= 10
    assert len(spec["channels"]["mz"]) == spec["returned_length"]


async def test_get_spectrum_out_of_range(
    session, empty_keyring, ms_file: Path
) -> None:
    reg = await handle_register(session, {"uri": str(ms_file)})
    search = await handle_search(session, {"file_id": reg["file_id"]})
    run_id = search["identifications"][0]["run_id"]

    with pytest.raises(InvalidArgument):
        await handle_get_spec(
            session,
            {"run_id": run_id, "spectrum_index": 999},
            keyring=empty_keyring,
        )


async def test_get_quantifications_basic(session, ms_file: Path) -> None:
    reg = await handle_register(session, {"uri": str(ms_file)})
    q = await handle_get_quant(session, {"file_id": reg["file_id"]})
    assert q["total"] == 2
    assert {row["chebi_id"] for row in q["quantifications"]} == {
        "CHEBI:15377",
        "CHEBI:28001",
    }


async def test_get_quantifications_filter(session, ms_file: Path) -> None:
    reg = await handle_register(session, {"uri": str(ms_file)})

    hit = await handle_get_quant(
        session, {"file_id": reg["file_id"], "chebi_id": "CHEBI:15377"}
    )
    assert hit["total"] == 1
    assert hit["quantifications"][0]["abundance"] == pytest.approx(1234.5)

    big = await handle_get_quant(
        session, {"file_id": reg["file_id"], "min_abundance": 1000}
    )
    assert big["total"] == 1
    assert big["quantifications"][0]["chebi_id"] == "CHEBI:15377"


async def test_get_quantifications_by_uri(session, ms_file: Path) -> None:
    await handle_register(session, {"uri": str(ms_file)})
    q = await handle_get_quant(session, {"uri": str(ms_file)})
    assert q["total"] == 2


async def test_tools_surface_has_all_14(session) -> None:
    from ttio_mcp.tools import TOOLS

    names = {t[0] for t in TOOLS}
    assert names == {
        "ttio_register_file",
        "ttio_list_files",
        "ttio_get_file",
        "ttio_reverify",
        "ttio_search_identifications",
        "ttio_get_run",
        "ttio_get_spectrum",
        "ttio_get_quantifications",
        "ttio_encrypt_file",
        "ttio_decrypt_file",
        "ttio_push_file",
        "ttio_sign_file",
        "ttio_verify_signature",
        "ttio_launch_uploader",
    }
