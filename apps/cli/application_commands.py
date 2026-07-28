from __future__ import annotations

import typer
from sqlalchemy.orm import Session

from packages.core.db import SessionLocal
from packages.core.domain.models import JobApplication
from packages.core.services import application_service
from packages.core.services.application_service import (
    ApplicationNotFoundError,
    InvalidApplicationTransitionError,
)
from packages.core.services.job_service import JobNotFoundError

applications_app = typer.Typer(help="Job application lifecycle commands")


@applications_app.command("create")
def create_draft(job_id: str) -> None:
    session: Session = SessionLocal()
    try:
        app = application_service.create_application_draft(session, job_id)
        session.commit()
        typer.echo(f"Application ID: {app.id} status={app.status}")
    except JobNotFoundError:
        typer.echo("Job not found")
        raise typer.Exit(code=1)
    finally:
        session.close()


@applications_app.command("show")
def show_application(application_id: str) -> None:
    session: Session = SessionLocal()
    try:
        app = application_service.get_application(session, application_id)
        typer.echo(f"ID: {app.id}")
        typer.echo(f"Job: {app.job_posting_id}")
        typer.echo(f"Track: {app.selected_track}")
        typer.echo(f"Status: {app.status}")
        typer.echo(f"Submitted at: {app.submitted_at}")
        typer.echo(f"Notes: {app.notes}")
    except ApplicationNotFoundError:
        typer.echo("Application not found")
        raise typer.Exit(code=1)
    finally:
        session.close()


@applications_app.command("list")
def list_applications(limit: int = 50) -> None:
    session: Session = SessionLocal()
    try:
        rows = (
            session.query(JobApplication)
            .order_by(JobApplication.updated_at.desc())
            .limit(limit)
            .all()
        )
        for app in rows:
            typer.echo(f"{app.id} | job={app.job_posting_id} | {app.status} | track={app.selected_track}")
    finally:
        session.close()


@applications_app.command("transition")
def transition(
    application_id: str,
    status: str,
    note: str | None = typer.Option(None, "--note"),
) -> None:
    session: Session = SessionLocal()
    try:
        app = application_service.transition_application(
            session, application_id, status, note=note
        )
        session.commit()
        typer.echo(f"Application {app.id} -> {app.status}")
    except ApplicationNotFoundError:
        typer.echo("Application not found")
        raise typer.Exit(code=1)
    except InvalidApplicationTransitionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2)
    finally:
        session.close()
