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
