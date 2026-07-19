from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from packages.core.settings import settings

# Create engine and session factory for application use
_engine: Engine = create_engine(settings.sqlalchemy_url())
SessionLocal = sessionmaker[Session](bind=_engine)


def get_engine() -> Engine:
    return _engine
