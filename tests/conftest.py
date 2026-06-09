from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from ttio_mcp.db import Base, make_engine, make_session_factory
from ttio_mcp.db.models import User
from ttio_mcp.keyring import Keyring

# Register cloud fixtures as a plugin so test modules can use
# ``moto_s3_server`` without importing it (which triggers F811).
pytest_plugins = ("tests._cloud",)


@pytest.fixture
def engine():
    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    # Match the M1 baseline migration: the 'system' user must exist
    # for any code path that resolves as_user. Real Alembic upgrades
    # insert this row; in-memory tests seed it here.
    factory = make_session_factory(eng)
    with factory() as s:
        s.add(User(name="system"))
        s.commit()
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session(engine) -> Session:
    factory = make_session_factory(engine)
    s = factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def empty_keyring() -> Keyring:
    """Empty Keyring that pairs with tests calling encryption-aware tools.

    Tests that exercise encrypt/decrypt flows build their own populated
    keyring via :class:`tests._fixtures` helpers; tests that just need
    a parameter slot use this one.
    """
    return Keyring.from_path(None)
