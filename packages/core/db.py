from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from packages.core.settings import settings

import os
from sqlalchemy.engine.url import make_url

# Create engine and session factory for application use.
# Tests must explicitly set TEST_SQL_ALCHEMY_URL to select an alternate DB.
# Relying on implicit environment markers like PYTEST_CURRENT_TEST is unsafe.
_engine: Engine
test_url = os.environ.get("TEST_SQL_ALCHEMY_URL")
if test_url:
    try:
        parsed = make_url(test_url)
        if parsed.drivername.startswith("postgres"):
            dev_parsed = make_url(settings.sqlalchemy_url())
            if parsed.host == dev_parsed.host and parsed.database == dev_parsed.database and not os.environ.get("ALLOW_TEST_ON_DEV_DB"):
                raise RuntimeError("TEST_SQL_ALCHEMY_URL matches the development DB. Set ALLOW_TEST_ON_DEV_DB=1 to override.")
    except Exception:
        pass
    _engine = create_engine(test_url)
else:
    _engine = create_engine(settings.sqlalchemy_url())

SessionLocal = sessionmaker[Session](bind=_engine)


def get_engine() -> Engine:
    return _engine
