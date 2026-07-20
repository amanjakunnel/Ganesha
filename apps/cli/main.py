"""CLI entrypoint for developer utilities (non-production).

Provides db-doctor which performs a safe connection test using the central
settings module. Never prints secrets or unredacted URLs.
"""
from __future__ import annotations

import sys
from typing import Any

import typer
from sqlalchemy import create_engine, text

from packages.core.settings import settings

app = typer.Typer(help="Developer CLI utilities")

# Import and attach jobs subcommands
from apps.cli.jobs import jobs_app  # noqa: E402,F401

app.add_typer(jobs_app, name="jobs")

# Import and attach decisions subcommands
from apps.cli.decisions_commands import decisions_app  # noqa: E402,F401

app.add_typer(decisions_app, name="decisions")

# Import and attach telegram subcommands
from apps.cli.telegram_cli import telegram_app  # noqa: E402,F401

app.add_typer(telegram_app, name="telegram")


@app.command("db-doctor")
def db_doctor() -> None:
    """Check database connectivity using central settings.

    Prints only non-sensitive connection details and performs a SELECT 1.
    Exits with non-zero status and concise recovery guidance on failure.
    """
    # Print safe connection info
    typer.echo(f"POSTGRES_USER={settings.postgres_user}")
    typer.echo(f"POSTGRES_DB={settings.postgres_db}")
    typer.echo(f"POSTGRES_HOST={settings.postgres_host}")
    typer.echo(f"POSTGRES_PORT={settings.postgres_port}")
    typer.echo(f"SQLALCHEMY_URL={settings.redacted_sqlalchemy_url()}")

    # Attempt a lightweight connection
    try:
        engine = create_engine(settings.sqlalchemy_url())
        with engine.connect() as conn:
            r = conn.execute(text("SELECT 1"))
            val = r.scalar()
        typer.secho(f"Connection test succeeded: {val}", fg=typer.colors.GREEN)
    except Exception as exc:  # pragma: no cover - exercise in runtime only
        typer.secho("Connection test failed", fg=typer.colors.RED)
        typer.echo("Error: " + str(exc))
        typer.echo("")
        typer.echo("Recovery guidance:")
        typer.echo(" - Ensure docker-compose is running the Postgres service")
        typer.echo(" - Ensure .env has POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB and you exported any overrides")
        typer.echo(" - Run: docker-compose down -v && docker-compose up -d && make db-doctor")
        raise typer.Exit(code=2)


def main(argv: list[str] | None = None) -> Any:  # pragma: no cover - entrypoint wrapper
    return app(prog_name="dev-cli", args=argv or sys.argv[1:])


@app.command("db-reset-local")
def db_reset_local(yes: bool = typer.Option(False, "--yes", help="Confirm destructive reset of the local dev database")) -> None:
    """Safely drop all tables in the configured local database and run migrations.

    This command refuses to run against a non-local host. It requires an explicit
    --yes confirmation to proceed. It drops all tables from the configured
    SQLALCHEMY URL and then runs alembic upgrade head to recreate schema.
    """
    # Use central settings to determine target DB and host
    url = settings.sqlalchemy_url()
    host = getattr(url, "host", None)
    if host not in ("localhost", "127.0.0.1", "::1", None):
        typer.secho(f"Refusing to reset non-local database host: {host}", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    if not yes:
        typer.echo("This will DROP ALL TABLES in the configured local database.")
        typer.echo("Re-run with --yes to confirm. Aborting.")
        raise typer.Exit(code=2)

    # Proceed with dropping and re-running migrations
    try:
        engine = create_engine(settings.sqlalchemy_url())
        from packages.core.domain.models import Base

        typer.echo("Dropping all tables...")
        Base.metadata.drop_all(bind=engine)
        typer.secho("Dropped local database tables.", fg=typer.colors.GREEN)

        typer.echo("Running migrations (alembic upgrade head)...")
        import subprocess

        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
        typer.secho("Migrations applied.", fg=typer.colors.GREEN)
    except Exception as exc:  # pragma: no cover - runtime tool
        typer.secho("Failed to reset local database:", fg=typer.colors.RED)
        typer.echo(str(exc))
        raise typer.Exit(code=3)


if __name__ == "__main__":
    # Allow a direct fast-path when invoked as a module with 'db-doctor'
    # to avoid CLI parsing surprises in some environments.
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == 'db-doctor':
        # Call the command directly and exit
        try:
            db_doctor()
            raise SystemExit(0)
        except SystemExit:
            raise
        except Exception:
            raise

    app()
