"""M5: ``as_user`` must resolve against the users table, not auto-create."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._fixtures import build_ms_fixture
from ttio_mcp.catalog import UnknownUser
from ttio_mcp.db.models import User
from ttio_mcp.tools.register import handle as handle_register


@pytest.fixture
def ms_file(tmp_path: Path) -> Path:
    return build_ms_fixture(tmp_path / "ms.mpgo")


async def test_as_user_unknown_raises(session, ms_file: Path) -> None:
    with pytest.raises(UnknownUser):
        await handle_register(
            session, {"uri": str(ms_file), "as_user": "nobody"}
        )


async def test_as_user_system_default_works(session, ms_file: Path) -> None:
    # No as_user → seeded 'system' user is used.
    from ttio_mcp.db.models import File

    reg = await handle_register(session, {"uri": str(ms_file)})
    assert reg["counts"]["runs"] == 1
    sys_user = session.query(User).filter(User.name == "system").one()
    row = session.get(File, reg["file_id"])
    assert row is not None
    assert row.registered_by == sys_user.id
    assert row.owner_user_id == sys_user.id


async def test_as_user_existing_is_accepted(session, ms_file: Path) -> None:
    from ttio_mcp.db.models import File

    session.add(User(name="alice"))
    session.commit()
    alice = session.query(User).filter(User.name == "alice").one()

    reg = await handle_register(
        session, {"uri": str(ms_file), "as_user": "alice"}
    )
    row = session.get(File, reg["file_id"])
    assert row is not None
    assert row.registered_by == alice.id
    assert row.owner_user_id == alice.id


async def test_unknown_user_does_not_autocreate(session, ms_file: Path) -> None:
    with pytest.raises(UnknownUser):
        await handle_register(
            session, {"uri": str(ms_file), "as_user": "mallory"}
        )
    # Confirm the row was not created as a side effect.
    assert session.query(User).filter(User.name == "mallory").count() == 0
