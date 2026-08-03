from __future__ import annotations

import sys
import subprocess

import typer
from sqlalchemy import create_engine, text

from packages.core.domain.models import Base
from packages.core.services.db_health import (
    DbFailureKind,
    check_database,
    format_db_connection_summary,
)
from packages.core.settings import settings

db_app = typer.Typer(help="Local database utilities")


@db_app.command("doctor")
def db_doctor_cmd() -> None:
    """Test database connectivity and report actionable recovery steps."""
    for line in format_db_connection_summary(settings):
        typer.echo(line)

    result = check_database(settings)
    if result.kind == DbFailureKind.OK:
        typer.secho(result.message, fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    typer.secho(f"Status: {result.kind.value}", fg=typer.colors.RED)
    typer.echo(result.message)
    if result.recovery:
        typer.echo("")
        for line in result.recovery:
            typer.echo(line)
    raise typer.Exit(code=2)


@db_app.command("reset-local")
def db_reset_local_cmd(
    yes: bool = typer.Option(
        False, "--yes", help="Confirm destructive reset of the local dev database"
    ),
) -> None:
    """Drop all local tables and re-run Alembic migrations."""
    url = settings.sqlalchemy_url()
    host = getattr(url, "host", None)
    if host not in ("localhost", "127.0.0.1", "::1", None):
        typer.secho(
            f"Refusing to reset non-local database host: {host}", fg=typer.colors.RED
        )
        raise typer.Exit(code=2)

    if not yes:
        typer.echo("This will DROP ALL TABLES in the configured local database.")
        typer.echo("Re-run with --yes to confirm. Aborting.")
        raise typer.Exit(code=2)

    precheck = check_database(settings)
    if precheck.kind == DbFailureKind.AUTH_FAILURE:
        typer.secho(
            "Cannot reset: database authentication failed.", fg=typer.colors.RED
        )
        typer.echo(precheck.message)
        typer.echo("")
        for line in precheck.recovery:
            typer.echo(line)
        raise typer.Exit(code=2)

    try:
        engine = create_engine(settings.sqlalchemy_url())
        typer.echo("Dropping all tables...")
        Base.metadata.drop_all(bind=engine)
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            conn.commit()
        typer.secho("Dropped local database tables.", fg=typer.colors.GREEN)

        typer.echo("Running migrations (alembic upgrade head)...")
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
        typer.secho("Migrations applied.", fg=typer.colors.GREEN)
    except Exception as exc:
        typer.secho("Failed to reset local database:", fg=typer.colors.RED)
        typer.echo(str(exc))
        raise typer.Exit(code=3)
