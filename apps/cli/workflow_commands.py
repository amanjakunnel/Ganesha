from __future__ import annotations

import json

import typer
from sqlalchemy.orm import Session

from packages.core.db import SessionLocal
from packages.core.services import workflow_service
from packages.core.services.job_service import JobNotFoundError

workflow_app = typer.Typer(help="End-to-end product workflow commands")


@workflow_app.command("demo")
def workflow_demo() -> None:
    """Run the full demo loop on a tagged demo job (idempotent)."""
    session: Session = SessionLocal()
    try:
        result = workflow_service.run_demo_loop(session)
        session.commit()
        typer.echo("Demo workflow complete:")
        for key, val in result.items():
            typer.echo(f"  {key}: {val}")
        typer.echo("")
        typer.echo("Next steps:")
        typer.echo("  .venv/bin/python -m apps.cli.main workflow dashboard")
        if result.get("decision_id"):
            typer.echo(
                f"  .venv/bin/python -m apps.cli.main decisions show {result['decision_id']}"
            )
    except Exception as exc:
        session.rollback()
        typer.secho(f"Demo failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    finally:
        session.close()


@workflow_app.command("dashboard")
def workflow_dashboard() -> None:
    session: Session = SessionLocal()
    try:
        summary = workflow_service.dashboard_summary(session)
        typer.echo(json.dumps(summary, indent=2, default=str))
    finally:
        session.close()


@workflow_app.command("queue")
def workflow_queue(limit: int = 20) -> None:
    session: Session = SessionLocal()
    try:
        items = workflow_service.list_actionable_queue(session, limit=limit)
        if not items:
            typer.echo("Actionable queue is empty.")
            return
        for item in items:
            typer.echo(
                f"{item.job_id} | {item.lifecycle} | {item.company} | {item.title[:60]} | {item.reason}"
            )
    finally:
        session.close()


@workflow_app.command("job")
def workflow_job(job_id: str) -> None:
    session: Session = SessionLocal()
    try:
        typer.echo(workflow_service.format_job_summary(session, job_id))
    except JobNotFoundError:
        typer.echo("Job not found")
        raise typer.Exit(code=1)
    finally:
        session.close()


@workflow_app.command("application")
def workflow_application(application_id: str) -> None:
    session: Session = SessionLocal()
    try:
        typer.echo(workflow_service.format_application_summary(session, application_id))
    except Exception:
        typer.echo("Application not found")
        raise typer.Exit(code=1)
    finally:
        session.close()
