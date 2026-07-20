import os
from sqlalchemy.engine.url import make_url

# Ensure tests run against an explicit test DB by default to avoid touching dev DB.
# If not provided, default to a local sqlite file.
if not os.environ.get("TEST_SQL_ALCHEMY_URL"):
    os.environ["TEST_SQL_ALCHEMY_URL"] = "sqlite:///./.pytest_test_db.sqlite"

# Safety: if TEST_SQL_ALCHEMY_URL points to the development DB, require explicit override
try:
    parsed = make_url(os.environ["TEST_SQL_ALCHEMY_URL"])
    if parsed.drivername.startswith("postgres"):
        # compare to dev DB
        from packages.core.settings import settings

        dev = make_url(settings.sqlalchemy_url())
        if parsed.host == dev.host and parsed.database == dev.database and not os.environ.get("ALLOW_TEST_ON_DEV_DB"):
            raise RuntimeError(
                "TEST_SQL_ALCHEMY_URL appears to target the development database."
                " Set ALLOW_TEST_ON_DEV_DB=1 to override if you know what you are doing."
            )
except Exception:
    # If parsing fails, let SQLAlchemy raise relevant errors when engine created.
    pass
