"""CLI entrypoint for developer utilities (non-production).

Provides db-doctor which performs a safe connection test using the central
settings module. Never prints secrets or unredacted URLs.
"""

from __future__ import annotations

import sys
from typing import Any

import typer

app = typer.Typer(help="Developer CLI utilities")

from apps.cli.db_commands import db_app  # noqa: E402

app.add_typer(db_app, name="db")

# Import and attach jobs subcommands
from apps.cli.jobs import jobs_app  # noqa: E402,F401

app.add_typer(jobs_app, name="jobs")

# Import and attach decisions subcommands
from apps.cli.decisions_commands import decisions_app  # noqa: E402,F401

app.add_typer(decisions_app, name="decisions")

# Import and attach telegram subcommands
from apps.cli.telegram_cli import telegram_app  # noqa: E402,F401

app.add_typer(telegram_app, name="telegram")

from apps.cli.fit_commands import fit_app  # noqa: E402

app.add_typer(fit_app, name="fit")

from apps.cli.application_commands import applications_app  # noqa: E402

app.add_typer(applications_app, name="applications")

from apps.cli.workflow_commands import workflow_app  # noqa: E402

app.add_typer(workflow_app, name="workflow")


@app.command("db-doctor", hidden=True)
def db_doctor_legacy() -> None:
    """Deprecated alias for `db doctor`."""
    from apps.cli.db_commands import db_doctor_cmd

    db_doctor_cmd()


@app.command("db-reset-local")
def db_reset_local_legacy(
    yes: bool = typer.Option(
        False, "--yes", help="Confirm destructive reset of the local dev database"
    ),
) -> None:
    """Alias for `db reset-local`."""
    from apps.cli.db_commands import db_reset_local_cmd

    db_reset_local_cmd(yes=yes)


def main(argv: list[str] | None = None) -> Any:  # pragma: no cover - entrypoint wrapper
    return app(prog_name="dev-cli", args=argv or sys.argv[1:])


if __name__ == "__main__":
    # Allow a direct fast-path when invoked as a module with 'db-doctor'
    # to avoid CLI parsing surprises in some environments.
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "db-doctor":
        from apps.cli.db_commands import db_doctor_cmd

        try:
            db_doctor_cmd()
            raise SystemExit(0)
        except SystemExit:
            raise
        except Exception:
            raise

    app()
