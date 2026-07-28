from __future__ import annotations

import typer
from sqlalchemy.orm import Session

from packages.core.db import SessionLocal
from packages.core.services import fit_service
from packages.core.services.job_service import JobNotFoundError

fit_app = typer.Typer(help="Resume and cover-letter fit commands")


@fit_app.command("build")
def build_fit(job_id: str) -> None:
    """Assess (if needed) and build a durable fit result for a job."""
    session: Session = SessionLocal()
    try:
        fit = fit_service.build_fit_result(session, job_id)
        session.commit()
        typer.echo(f"Fit ID: {fit.id}")
        typer.echo(f"Track: {fit.recommended_track}")
        typer.echo(f"Readiness: {fit.readiness_status}")
        typer.echo(f"Next action: {fit.next_action}")
        if fit.key_requirements:
            typer.echo("Key requirements:")
            for req in fit.key_requirements[:5]:
                typer.echo(f"  - {req}")
    except JobNotFoundError:
        typer.echo("Job not found")
        raise typer.Exit(code=1)
    finally:
        session.close()


@fit_app.command("show")
def show_fit(job_id: str) -> None:
    session: Session = SessionLocal()
    try:
        fit = fit_service.get_fit_result(session, job_id)
        if fit is None:
            typer.echo("No fit result for this job. Run: fit build <job_id>")
            raise typer.Exit(code=1)
        typer.echo(f"Fit ID: {fit.id}")
        typer.echo(f"Track: {fit.recommended_track}")
        typer.echo(f"Readiness: {fit.readiness_status}")
        typer.echo(f"Next action: {fit.next_action}")
        typer.echo(f"Key requirements: {fit.key_requirements}")
        typer.echo(f"Missing evidence: {fit.missing_evidence}")
    finally:
        session.close()
