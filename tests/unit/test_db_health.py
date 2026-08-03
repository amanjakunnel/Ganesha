from __future__ import annotations

from unittest.mock import MagicMock, patch

from sqlalchemy.exc import OperationalError

from packages.core.services.db_health import (
    AUTH_MISMATCH_RECOVERY,
    DbFailureKind,
    TELEGRAM_DB_UNAVAILABLE_MESSAGE,
    check_database,
    classify_db_error,
    format_db_connection_summary,
    is_database_unavailable,
)
from packages.core.settings import Settings


def test_classify_auth_failure() -> None:
    exc = OperationalError(
        "SELECT 1", {}, Exception("password authentication failed for user")
    )
    assert classify_db_error(exc) == DbFailureKind.AUTH_FAILURE


def test_classify_connection_refused() -> None:
    exc = OperationalError("connect", {}, Exception("connection refused"))
    assert classify_db_error(exc) == DbFailureKind.CONNECTION_REFUSED


def test_classify_schema_not_migrated() -> None:
    exc = OperationalError(
        "SELECT", {}, Exception('relation "job_postings" does not exist')
    )
    assert classify_db_error(exc) == DbFailureKind.SCHEMA_NOT_MIGRATED


def test_is_database_unavailable_operational_error() -> None:
    assert is_database_unavailable(OperationalError("stmt", {}, Exception("fail")))


def test_auth_recovery_includes_volume_warning_and_steps() -> None:
    joined = "\n".join(AUTH_MISMATCH_RECOVERY)
    assert "docker compose down -v" in joined
    assert "deletes the local Postgres volume" in joined
    assert "db-reset-local --yes" in joined
    assert "jobs import-sheets" in joined


def test_telegram_db_unavailable_message_has_no_secrets() -> None:
    assert "password" not in TELEGRAM_DB_UNAVAILABLE_MESSAGE.lower()
    assert "db doctor" in TELEGRAM_DB_UNAVAILABLE_MESSAGE


def test_format_db_connection_summary_never_includes_password() -> None:
    settings = Settings(
        postgres_user="job_agent",
        postgres_password="secret_value",
        postgres_db="job_agent",
        postgres_host="localhost",
        postgres_port=5432,
    )
    lines = format_db_connection_summary(settings)
    joined = "\n".join(lines)
    assert "secret_value" not in joined
    assert "user=job_agent" in joined
    assert "host=localhost" in joined


def test_check_database_auth_failure() -> None:
    settings = Settings(postgres_user="job_agent", postgres_password="wrong")
    engine = MagicMock()
    engine.connect.side_effect = OperationalError(
        "connect",
        {},
        Exception("password authentication failed for user job_agent"),
    )
    result = check_database(settings, engine=engine)
    assert result.kind == DbFailureKind.AUTH_FAILURE
    assert "authentication failed" in result.message.lower()
    assert result.recovery == list(AUTH_MISMATCH_RECOVERY)


def test_check_database_ok() -> None:
    settings = Settings()
    conn = MagicMock()
    conn.execute.return_value.scalar.return_value = 1
    inspector = MagicMock()
    inspector.get_table_names.return_value = [
        "job_postings",
        "alembic_version",
        "audit_events",
    ]

    engine = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    engine.connect.return_value = cm

    with patch("packages.core.services.db_health.inspect", return_value=inspector):
        result = check_database(settings, engine=engine)

    assert result.kind == DbFailureKind.OK
    assert result.select_one_ok is True


def test_check_database_schema_not_migrated() -> None:
    settings = Settings()
    conn = MagicMock()
    conn.execute.return_value.scalar.return_value = 1
    inspector = MagicMock()
    inspector.get_table_names.return_value = ["alembic_version"]

    engine = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    engine.connect.return_value = cm

    with patch("packages.core.services.db_health.inspect", return_value=inspector):
        result = check_database(settings, engine=engine)

    assert result.kind == DbFailureKind.SCHEMA_NOT_MIGRATED
    assert result.select_one_ok is True
