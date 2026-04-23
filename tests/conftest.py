from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from mpeg_o_mcp.db import Base, make_engine, make_session_factory


@pytest.fixture
def engine():
    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
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
