"""M2: direct catalog / registration round-trips without going through MCP."""
from __future__ import annotations

from pathlib import Path

import pytest

from ttio_mcp.catalog import (
    InvalidURI,
    NotFound,
    ResolveFailed,
    canonical_uri,
    register_file,
    resolve_local_path,
)
from ttio_mcp.db.models import (
    File,
    Identification,
    ProvenanceRecord,
    Quantification,
    Run,
    Study,
)
from ttio_mcp.hashes import hash_file_sha256
from tests._fixtures import build_ms_fixture, build_nmr_fixture


@pytest.fixture
def ms_file(tmp_path: Path) -> Path:
    return build_ms_fixture(tmp_path / "ms.mpgo")


@pytest.fixture
def nmr_file(tmp_path: Path) -> Path:
    return build_nmr_fixture(tmp_path / "nmr.mpgo")


def test_hash_file_sha256_is_stable(ms_file: Path) -> None:
    a = hash_file_sha256(ms_file)
    b = hash_file_sha256(ms_file)
    assert a == b
    assert len(a) == 64


def test_resolve_bare_path(ms_file: Path) -> None:
    p = resolve_local_path(str(ms_file))
    assert p == ms_file.resolve()
    canon = canonical_uri(p)
    assert canon.startswith("file://")


def test_resolve_rejects_s3() -> None:
    with pytest.raises(InvalidURI):
        resolve_local_path("s3://bucket/key.mpgo")


def test_resolve_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ResolveFailed):
        resolve_local_path(str(tmp_path / "does-not-exist.mpgo"))


def test_register_ms_roundtrip(session, ms_file: Path) -> None:
    result = register_file(session, str(ms_file))

    assert result.was_update is False
    assert result.counts == {
        "studies": 1,
        "runs": 1,
        "identifications": 2,
        "quantifications": 2,
        "provenance_records": 1,
    }
    assert result.format_version == "1.1"
    assert "base_v1" in result.features

    # Check rows landed correctly.
    f = session.get(File, result.file_id)
    assert f is not None
    assert f.file_sha256 == result.file_sha256
    assert len(f.studies) == 1
    assert f.studies[0].title == "demo-ms"
    assert len(f.runs) == 1
    run = f.runs[0]
    assert run.acquisition_mode == "MS1_DDA"
    assert run.spectrum_count == 5
    assert run.polarity == "positive"
    ids = session.query(Identification).filter(Identification.file_id == f.id).all()
    assert len(ids) == 2
    assert {i.chebi_id for i in ids} == {"CHEBI:15377", "CHEBI:28001"}
    assert all(i.run_id == run.id for i in ids)
    provs = session.query(ProvenanceRecord).filter(ProvenanceRecord.file_id == f.id).all()
    assert len(provs) == 1
    assert provs[0].software == "demo-writer 1.0"
    assert provs[0].input_refs == ["urn:raw:sample-a"]
    quants = session.query(Quantification).filter(Quantification.file_id == f.id).all()
    assert len(quants) == 2
    assert {q.chebi_id for q in quants} == {"CHEBI:15377", "CHEBI:28001"}
    assert all(q.sample_ref == "run_0001" for q in quants)


def test_register_nmr_roundtrip(session, nmr_file: Path) -> None:
    result = register_file(session, str(nmr_file))
    f = session.get(File, result.file_id)
    assert len(f.runs) == 1
    run = f.runs[0]
    assert run.acquisition_mode == "NMR_1D"
    assert (run.metadata_json or {}).get("nucleus_type") == "1H"


def test_register_is_idempotent(session, ms_file: Path) -> None:
    first = register_file(session, str(ms_file))
    second = register_file(session, str(ms_file))

    assert first.file_id == second.file_id
    assert second.was_update is True

    # Exactly one file row, and child counts match exactly once (no
    # leftovers from the first insert).
    assert session.query(File).count() == 1
    assert session.query(Study).count() == 1
    assert session.query(Run).count() == 1
    assert session.query(Identification).count() == 2
    assert session.query(Quantification).count() == 2
    assert session.query(ProvenanceRecord).count() == 1


def test_register_not_mpgo(session, tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.mpgo"
    bogus.write_bytes(b"not an hdf5 file")
    from ttio_mcp.catalog import NotMpeg

    with pytest.raises(NotMpeg):
        register_file(session, str(bogus))


def test_lookup_by_uri_and_id(session, ms_file: Path) -> None:
    result = register_file(session, str(ms_file))
    from ttio_mcp.tools._helpers import lookup_file

    by_id = lookup_file(session, id_or_uri={"id": result.file_id})
    by_uri = lookup_file(session, id_or_uri={"uri": str(ms_file)})
    assert by_id.id == by_uri.id == result.file_id

    with pytest.raises(NotFound):
        lookup_file(session, id_or_uri={"id": 99999})
