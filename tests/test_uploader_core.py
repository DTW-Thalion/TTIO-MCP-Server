"""Pure-logic tests for ``mpeg_o_mcp.uploader.core`` — no GUI, no subprocess."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mpeg_o_mcp.uploader.core import (
    IMPORTABLE_EXTENSIONS,
    copy_to_intake,
    detect_format,
    get_intake_dir,
)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("sample.mpgo", "mpgo"),
        ("SAMPLE.MPGO", "mpgo"),
        ("run.mzml", "mzml"),
        ("run.mzML", "mzml"),
        ("spectra.nmrml", "nmrml"),
        ("image.imzml", "imzml"),
        ("table.mztab", "mztab"),
        ("unknown.txt", None),
        ("noext", None),
    ],
)
def test_detect_format(name: str, expected: str | None) -> None:
    assert detect_format(Path(name)) == expected


def test_importable_extensions_are_lowercase_and_dotted() -> None:
    for ext, fmt in IMPORTABLE_EXTENSIONS.items():
        assert ext.startswith(".") and ext == ext.lower()
        assert fmt and fmt == fmt.lower()


def test_get_intake_dir_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MPGO_MCP_INTAKE_DIR", raising=False)
    assert get_intake_dir() is None


def test_get_intake_dir_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MPGO_MCP_INTAKE_DIR", "   ")
    assert get_intake_dir() is None


def test_get_intake_dir_resolves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MPGO_MCP_INTAKE_DIR", str(tmp_path))
    out = get_intake_dir()
    assert out == tmp_path.resolve()


def test_copy_to_intake_creates_dir_and_copies(tmp_path: Path) -> None:
    src = tmp_path / "src.mpgo"
    src.write_bytes(b"hello")
    intake = tmp_path / "intake"

    dest = copy_to_intake(src, intake)
    assert dest == intake / "src.mpgo"
    assert dest.read_bytes() == b"hello"
    assert intake.is_dir()


def test_copy_to_intake_timestamps_on_collision(tmp_path: Path) -> None:
    src = tmp_path / "sample.mpgo"
    src.write_bytes(b"content A")
    intake = tmp_path / "intake"
    intake.mkdir()
    (intake / "sample.mpgo").write_bytes(b"already there")

    fixed = datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)
    dest = copy_to_intake(src, intake, now=fixed)

    assert dest.name == "sample-20260424T120000Z.mpgo"
    assert dest.read_bytes() == b"content A"
    # Original was preserved.
    assert (intake / "sample.mpgo").read_bytes() == b"already there"


def test_copy_to_intake_counter_on_double_collision(tmp_path: Path) -> None:
    src = tmp_path / "sample.mpgo"
    src.write_bytes(b"new")
    intake = tmp_path / "intake"
    intake.mkdir()
    (intake / "sample.mpgo").write_bytes(b"a")
    fixed = datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)
    (intake / "sample-20260424T120000Z.mpgo").write_bytes(b"b")

    dest = copy_to_intake(src, intake, now=fixed)
    assert dest.name == "sample-20260424T120000Z-1.mpgo"
    assert dest.read_bytes() == b"new"


def test_copy_to_intake_overwrite(tmp_path: Path) -> None:
    src = tmp_path / "sample.mpgo"
    src.write_bytes(b"new bytes")
    intake = tmp_path / "intake"
    intake.mkdir()
    (intake / "sample.mpgo").write_bytes(b"stale")

    dest = copy_to_intake(src, intake, overwrite=True)
    assert dest == intake / "sample.mpgo"
    assert dest.read_bytes() == b"new bytes"


def test_copy_to_intake_rejects_non_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        copy_to_intake(tmp_path / "nope.mpgo", tmp_path / "intake")


def test_copy_to_intake_emits_progress(tmp_path: Path) -> None:
    src = tmp_path / "big.mpgo"
    # 3.5 chunks worth at the 1 KiB chunk size we pass in.
    src.write_bytes(b"x" * 3584)
    intake = tmp_path / "intake"

    events: list[tuple[int, int]] = []
    dest = copy_to_intake(
        src,
        intake,
        progress=lambda copied, total: events.append((copied, total)),
        chunk_size=1024,
    )

    assert dest.read_bytes() == b"x" * 3584
    # Expect one callback per chunk (4 chunks: 1024, 1024, 1024, 512)
    # plus the final (total, total) completion signal.
    assert [copied for copied, _ in events] == [1024, 2048, 3072, 3584, 3584]
    assert all(total == 3584 for _, total in events)


def test_copy_to_intake_progress_on_collision_path(tmp_path: Path) -> None:
    src = tmp_path / "sample.mpgo"
    src.write_bytes(b"a" * 2048)
    intake = tmp_path / "intake"
    intake.mkdir()
    (intake / "sample.mpgo").write_bytes(b"existing")

    events: list[int] = []
    dest = copy_to_intake(
        src,
        intake,
        progress=lambda copied, _total: events.append(copied),
        chunk_size=1024,
    )
    # Timestamped destination still reports progress.
    assert dest.name != "sample.mpgo"
    assert events[-1] == 2048


def test_copy_to_intake_cleans_up_partial_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "big.mpgo"
    src.write_bytes(b"x" * 3072)
    intake = tmp_path / "intake"

    class Boom(RuntimeError):
        pass

    def explode(copied: int, _total: int) -> None:
        if copied >= 2048:
            raise Boom("simulated UI thread failure")

    with pytest.raises(Boom):
        copy_to_intake(src, intake, progress=explode, chunk_size=1024)

    # Partial file must be gone so a retry hits the natural (non-colliding) path.
    assert list(intake.iterdir()) == []
