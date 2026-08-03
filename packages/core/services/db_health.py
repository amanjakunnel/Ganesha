from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from packages.core.settings import Settings

AUTH_MISMATCH_RECOVERY: tuple[str, ...] = (
    "WARNING: `docker compose down -v` deletes the local Postgres volume.",
    "Only use this when local database data can be recreated.",
    "",
    "Recovery steps:",
    "  1. docker compose down -v",
    "  2. docker compose up -d",
    "  3. python -m apps.cli.main db-reset-local --yes",
    "  4. python -m apps.cli.main jobs import-sheets",
)


class DbFailureKind(str, Enum):
    OK = "ok"
    AUTH_FAILURE = "auth_failure"
    CONNECTION_REFUSED = "connection_refused"
    SERVICE_UNAVAILABLE = "service_unavailable"
    SCHEMA_NOT_MIGRATED = "schema_not_migrated"
    UNKNOWN = "unknown"


@dataclass
class DbCheckResult:
    kind: DbFailureKind
    message: str
    recovery: list[str] = field(default_factory=list)
    select_one_ok: bool = False


TELEGRAM_DB_UNAVAILABLE_MESSAGE = (
    "Database unavailable: Ganesha cannot access local PostgreSQL. "
    "Run `python -m apps.cli.main db doctor` locally, fix the database, then retry."
)


def classify_db_error(exc: BaseException) -> DbFailureKind:
    text_lower = str(exc).lower()
    if (
        "password authentication failed" in text_lower
        or "authentication failed" in text_lower
    ):
        return DbFailureKind.AUTH_FAILURE
    if (
        "connection refused" in text_lower
        or "could not connect to server" in text_lower
    ):
        return DbFailureKind.CONNECTION_REFUSED
    if (
        "could not translate host" in text_lower
        or "name or service not known" in text_lower
    ):
        return DbFailureKind.CONNECTION_REFUSED
    if "does not exist" in text_lower and (
        "relation" in text_lower
        or "table" in text_lower
        or "alembic_version" in text_lower
    ):
        return DbFailureKind.SCHEMA_NOT_MIGRATED
    if "timeout" in text_lower or "timed out" in text_lower:
        return DbFailureKind.SERVICE_UNAVAILABLE
    if isinstance(exc, OperationalError):
        return DbFailureKind.SERVICE_UNAVAILABLE
    return DbFailureKind.UNKNOWN


def is_database_unavailable(exc: BaseException) -> bool:
    if isinstance(exc, (OperationalError, SQLAlchemyError)):
        return True
    kind = classify_db_error(exc)
    return kind != DbFailureKind.OK


def _recovery_for_kind(kind: DbFailureKind) -> list[str]:
    if kind == DbFailureKind.AUTH_FAILURE:
        return list(AUTH_MISMATCH_RECOVERY)
    if kind == DbFailureKind.CONNECTION_REFUSED:
        return [
            "Postgres does not appear reachable at the configured host/port.",
            "Start it with: docker compose up -d",
            "Then run: python -m apps.cli.main db doctor",
        ]
    if kind == DbFailureKind.SCHEMA_NOT_MIGRATED:
        return [
            "Database is reachable but schema is missing or incomplete.",
            "Run: python -m apps.cli.main db-reset-local --yes",
            "Or: make migrate",
        ]
    if kind == DbFailureKind.SERVICE_UNAVAILABLE:
        return [
            "Database service is unreachable or timing out.",
            "Check: docker compose ps",
            "Then run: python -m apps.cli.main db doctor",
        ]
    return [
        "Check Docker Postgres and .env database settings.",
        "Run: python -m apps.cli.main db doctor",
    ]


def check_database(
    settings: Settings, *, engine: Engine | None = None
) -> DbCheckResult:
    """Test DB connectivity and basic schema presence."""
    owns_engine = engine is None
    if owns_engine:
        engine = create_engine(settings.sqlalchemy_url())
    assert engine is not None

    try:
        with engine.connect() as conn:
            val = conn.execute(text("SELECT 1")).scalar()
            if val != 1:
                return DbCheckResult(
                    kind=DbFailureKind.UNKNOWN,
                    message="SELECT 1 returned unexpected result.",
                    recovery=_recovery_for_kind(DbFailureKind.UNKNOWN),
                )

        with engine.connect() as conn:
            inspector = inspect(conn)
            tables = set(inspector.get_table_names())
            if "job_postings" not in tables or "alembic_version" not in tables:
                return DbCheckResult(
                    kind=DbFailureKind.SCHEMA_NOT_MIGRATED,
                    message="Database connected but required tables are missing.",
                    recovery=_recovery_for_kind(DbFailureKind.SCHEMA_NOT_MIGRATED),
                    select_one_ok=True,
                )
    except Exception as exc:
        kind = classify_db_error(exc)
        return DbCheckResult(
            kind=kind,
            message=_public_error_summary(kind, exc),
            recovery=_recovery_for_kind(kind),
        )
    finally:
        if owns_engine:
            engine.dispose()

    return DbCheckResult(
        kind=DbFailureKind.OK,
        message="Database connection and schema check succeeded.",
        select_one_ok=True,
    )


def _public_error_summary(kind: DbFailureKind, exc: BaseException) -> str:
    if kind == DbFailureKind.AUTH_FAILURE:
        return (
            "Password authentication failed for the configured Postgres user. "
            "The Docker volume may have been initialized with different credentials than .env."
        )
    if kind == DbFailureKind.CONNECTION_REFUSED:
        return "Could not connect to Postgres (connection refused or host unreachable)."
    if kind == DbFailureKind.SCHEMA_NOT_MIGRATED:
        return "Connected to Postgres but the application schema is not migrated."
    if kind == DbFailureKind.SERVICE_UNAVAILABLE:
        return "Postgres is not responding (timeout or service unavailable)."
    return f"Database check failed: {exc.__class__.__name__}"


def format_db_connection_summary(settings: Settings) -> list[str]:
    return [
        f"host={settings.postgres_host}",
        f"port={settings.postgres_port}",
        f"database={settings.postgres_db}",
        f"user={settings.postgres_user}",
        f"url={settings.redacted_sqlalchemy_url()}",
    ]
