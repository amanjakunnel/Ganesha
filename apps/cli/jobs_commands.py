"""Job intake and triage commands (non-production local use only)."""
from __future__ import annotations

import sys
from datetime import datetime

import typer
from sqlalchemy.orm import Session

from packages.core.db import SessionLocal
from packages.core.services.job_service import (
    import_csv,
    import_json,
    assess_job,
    list_review_queue,
    start_referral,
)
from packages.core.domain.models import JobPosting, ReferralTask, AuditEvent

jobs_app = typer.Typer(help="Job intake and triage commands")


@jobs_app.command(name="import-csv")
def import_csv_cmd(path: str) -> None:
    """Import jobs from a CSV file."""
    session: Session = SessionLocal()
    try:
        res = import_csv(session, path)
        session.commit()
        typer.echo(f"Imported: {res.created}, Duplicates: {res.duplicates}, Errors: {len(res.errors)}")
        for e in (res.errors or []):
            typer.echo(f"Error: {e}")
    finally:
        session.close()


@jobs_app.command(name="import-json")
def import_json_cmd(path: str) -> None:
    """Import jobs from a JSON file."""
    session: Session = SessionLocal()
    try:
        res = import_json(session, path)
        session.commit()
        typer.echo(f"Imported: {res.created}, Duplicates: {res.duplicates}, Errors: {len(res.errors)}")
    finally:
        session.close()


@jobs_app.command()
def add(
    title: str, company: str, description_file: str | None = None
) -> None:
    """Manually add a job posting."""
    session: Session = SessionLocal()
    try:
        if description_file:
            with open(description_file, encoding="utf-8") as fh:
                description = fh.read()
        else:
            typer.echo("Enter description (end with EOF / Ctrl-D):")
            description = sys.stdin.read()
        data = {"title": title, "company": company, "description_text": description}
        from packages.core.services.job_service import _create_or_flag

        created = _create_or_flag(session, data, source="manual")
        session.commit()
        typer.echo(f"Created: {created}")
    finally:
        session.close()


@jobs_app.command()
def list() -> None:
    """List recent job postings."""
    session: Session = SessionLocal()
    try:
        rows = session.query(JobPosting).order_by(JobPosting.created_at.desc()).limit(100).all()
        for r in rows:
            company_name = r.company.canonical_name if r.company else "Unknown"
            typer.echo(f"{r.id} | {r.title} | {r.status} | {company_name}")
    finally:
        session.close()


@jobs_app.command()
def show(job_id: str) -> None:
    """Show details of a specific job posting."""
    session: Session = SessionLocal()
    try:
        j = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
        if j is None:
            typer.echo("Not found")
            raise typer.Exit(code=1)
        typer.echo(f"ID: {j.id}")
        typer.echo(f"Title: {j.title}")
        company_name = j.company.canonical_name if j.company else "Unknown"
        typer.echo(f"Company: {company_name}")
        typer.echo(f"Location: {j.location}")
        typer.echo(f"Status: {j.status}")
        desc_text = j.description_text or ""
        excerpt = desc_text[:500] + ("..." if len(desc_text) > 500 else "")
        typer.echo(f"Description:\n{excerpt}")
    finally:
        session.close()


@jobs_app.command()
def assess(job_id: str) -> None:
    """Assess a job posting and recommend a resume track."""
    session: Session = SessionLocal()
    try:
        a = assess_job(session, job_id)
        session.commit()
        typer.echo(f"Recommended track: {a.recommended_track}, score: {a.score}")
        typer.echo(f"Explanation: {a.score_explanation}")
    finally:
        session.close()


@jobs_app.command(name="queue")
def queue_cmd(limit: int = 20) -> None:
    """List actionable jobs (alias for workflow queue)."""
    from packages.core.services import workflow_service

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


@jobs_app.command()
def triage() -> None:
    """List jobs queued for review."""
    session: Session = SessionLocal()
    try:
        qs = list_review_queue(session)
        for j in qs:
            typer.echo(f"{j.id} | {j.title[:50]} | {j.status}")
    finally:
        session.close()


@jobs_app.command()
def approve(job_id: str, confirm: bool = typer.Option(False, "--confirm")) -> None:
    """Approve a job posting for application."""
    if not confirm:
        typer.echo("This is a destructive action. Re-run with --confirm to proceed.")
        raise typer.Exit(code=2)
    session: Session = SessionLocal()
    try:
        j = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
        if j is None:
            typer.echo("Not found")
            raise typer.Exit(code=1)
        j.status = "approved"
        session.add(j)
        session.commit()
        typer.echo("Approved")
    finally:
        session.close()


@jobs_app.command()
def skip(job_id: str, confirm: bool = typer.Option(False, "--confirm")) -> None:
    """Skip a job posting."""
    if not confirm:
        typer.echo("This is a destructive action. Re-run with --confirm to proceed.")
        raise typer.Exit(code=2)
    session: Session = SessionLocal()
    try:
        j = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
        if j is None:
            typer.echo("Not found")
            raise typer.Exit(code=1)
        j.status = "skipped"
        session.add(j)
        session.commit()
        typer.echo("Skipped")
    finally:
        session.close()


@jobs_app.command()
def defer(job_id: str, until: str = typer.Option(..., help="YYYY-MM-DD")) -> None:
    """Defer review of a job posting until a specific date."""
    session: Session = SessionLocal()
    try:
        j = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
        if j is None:
            typer.echo("Not found")
            raise typer.Exit(code=1)
        _until = datetime.fromisoformat(until)
        j.status = "deferred"
        session.add(j)
        ev = AuditEvent(entity_type="job_posting", entity_id=j.id, event_type="deferred", payload={"until": until})
        session.add(ev)
        session.commit()
        typer.echo("Deferred")
    finally:
        session.close()


@jobs_app.command(name="referral-start")
def referral_start_cmd(job_id: str, cutoff_hours: int = 48) -> None:
    """Start a referral task for a job posting with a 48-hour cutoff."""
    session: Session = SessionLocal()
    try:
        try:
            rt = start_referral(session, job_id, cutoff_hours=cutoff_hours)
            session.commit()
        except Exception as exc:
            from packages.core.services.job_service import JobNotFoundError

            if isinstance(exc, JobNotFoundError):
                typer.echo(f"Job not found: {job_id}")
                raise typer.Exit(code=1)
            # re-raise unexpected exceptions
            raise

        # Attempt to find a linked decision request created by start_referral
        from packages.core.domain.models import DecisionRequest

        dec = (
            session.query(DecisionRequest)
            .filter(DecisionRequest.idempotency_key == f"referral:{rt.id}")
            .one_or_none()
        )
        if dec:
            typer.echo(f"Referral task started, cutoff_at={rt.cutoff_at}, decision_id={dec.id}")
        else:
            typer.echo(f"Referral task started, cutoff_at={rt.cutoff_at}")
    finally:
        session.close()


@jobs_app.command(name="referral-status")
def referral_status_cmd(job_id: str) -> None:
    """Check the status of a referral task."""
    session: Session = SessionLocal()
    try:
        r = session.query(ReferralTask).filter(ReferralTask.job_posting_id == job_id).one_or_none()
        if r is None:
            typer.echo("No referral task")
            raise typer.Exit(code=1)
        typer.echo(f"status={r.status}, cutoff_at={r.cutoff_at}")
    finally:
        session.close()
