from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import inspect

from mpeg_o_mcp.db import File, Identification, ProvenanceRecord, Run, Study, User

REPO_ROOT = Path(__file__).resolve().parent.parent


EXPECTED_TABLES = {
    "users",
    "files",
    "studies",
    "runs",
    "identifications",
    "provenance_records",
}


def test_all_tables_exist(engine):
    names = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.issubset(names), f"missing: {EXPECTED_TABLES - names}"


def test_insert_and_cascade_delete(session):
    user = User(name="alice")
    session.add(user)
    session.flush()

    f = File(
        uri="file:///sample.mpgo",
        file_sha256="a" * 64,
        content_sha256="b" * 64,
        format_version="1.3",
        features={},
        encrypted=False,
        signed=False,
        registered_by=user.id,
        owner_user_id=user.id,
    )
    session.add(f)
    session.flush()

    study = Study(file_id=f.id, title="study-1")
    run = Run(file_id=f.id, name="run-1", acquisition_mode="DDA")
    prov = ProvenanceRecord(file_id=f.id, software="mpeg-o 1.0.0")
    session.add_all([study, run, prov])
    session.flush()

    ident = Identification(
        file_id=f.id, run_id=run.id, chebi_id="CHEBI:15377", name="water", score=0.99
    )
    session.add(ident)
    session.flush()

    assert session.query(Study).count() == 1
    assert session.query(Run).count() == 1
    assert session.query(Identification).count() == 1
    assert session.query(ProvenanceRecord).count() == 1

    session.delete(f)
    session.flush()

    assert session.query(Study).count() == 0
    assert session.query(Run).count() == 0
    assert session.query(Identification).count() == 0
    assert session.query(ProvenanceRecord).count() == 0
    assert session.query(User).count() == 1  # users NOT cascaded


def _run_alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={
            "PATH": __import__("os").environ["PATH"],
            "MPGO_MCP_DB_URL": db_url,
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_alembic_upgrade_seeds_system_user(tmp_path):
    db = tmp_path / "alembic.db"
    url = f"sqlite:///{db}"

    up = _run_alembic(url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    eng = sa.create_engine(url)
    try:
        with eng.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    sa.text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            }
            assert EXPECTED_TABLES.issubset(tables)

            rows = conn.execute(sa.text("SELECT id, name FROM users")).fetchall()
            assert rows == [(1, "system")]
    finally:
        eng.dispose()

    down = _run_alembic(url, "downgrade", "base")
    assert down.returncode == 0, down.stderr

    eng = sa.create_engine(url)
    try:
        with eng.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    sa.text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            }
            # only alembic_version should remain
            assert tables <= {"alembic_version"}
    finally:
        eng.dispose()
