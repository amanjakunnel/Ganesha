"""Job intake and triage commands (non-production local use only)."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

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

SHEETS_DIR = Path("sheets")
DEFAULT_LINKEDIN_CSV = SHEETS_DIR / "JobsScraperForLinkedIn_13_2026-07-29.csv"
DEFAULT_SYMPLICITY_XLSX = SHEETS_DIR / "Symplicity Example.xlsx"
DEFAULT_REFERRAL_XLSX = SHEETS_DIR / "Referral Contacts.xlsx"


def _print_import_result(label: str, res: object) -> None:
    from packages.core.services.job_intake import ImportResult

    if not isinstance(res, ImportResult):
        return
    typer.echo(
        f"{label}: created={res.created}, updated={getattr(res, 'updated', 0)}, "
        f"duplicates={res.duplicates}, errors={len(res.errors)}"
    )
    for e in res.errors[:20]:
        typer.echo(f"  error: {e}")
    if len(res.errors) > 20:
        typer.echo(f"  ... and {len(res.errors) - 20} more errors")


@jobs_app.command(name="import-linkedin")
def import_linkedin_cmd(
    path: str = typer.Option(str(DEFAULT_LINKEDIN_CSV), "--path", help="LinkedIn scraper CSV"),
) -> None:
    """Import jobs from the LinkedIn CSV export in sheets/."""
    from packages.core.services.importers.linkedin_csv import import_linkedin_csv

    session: Session = SessionLocal()
    try:
        res = import_linkedin_csv(session, path)
        session.commit()
        _print_import_result("LinkedIn import", res)
    finally:
        session.close()


@jobs_app.command(name="import-symplicity")
def import_symplicity_cmd(
    path: str = typer.Option(str(DEFAULT_SYMPLICITY_XLSX), "--path", help="Symplicity XLSX"),
) -> None:
    """Import jobs from a manual Symplicity XLSX export."""
    from packages.core.services.importers.symplicity_xlsx import import_symplicity_xlsx

    session: Session = SessionLocal()
    try:
        res = import_symplicity_xlsx(session, path)
        session.commit()
        _print_import_result("Symplicity import", res)
    finally:
        session.close()


@jobs_app.command(name="import-referrals")
def import_referrals_cmd(
    path: str = typer.Option(str(DEFAULT_REFERRAL_XLSX), "--path", help="Referral contacts XLSX"),
) -> None:
    """Import referral contact enrichment data (not jobs)."""
    from packages.core.services.importers.referral_contacts_xlsx import import_referral_contacts_xlsx

    session: Session = SessionLocal()
    try:
        res = import_referral_contacts_xlsx(session, path)
        session.commit()
        _print_import_result("Referral contacts import", res)
    finally:
        session.close()


@jobs_app.command(name="import-sheets")
def import_sheets_cmd() -> None:
    """Import LinkedIn, Symplicity, and referral contacts from Ganesha/sheets."""
    from packages.core.services.importers.linkedin_csv import import_linkedin_csv
    from packages.core.services.importers.referral_contacts_xlsx import import_referral_contacts_xlsx
    from packages.core.services.importers.symplicity_xlsx import import_symplicity_xlsx

    session: Session = SessionLocal()
    try:
        li = import_linkedin_csv(session, str(DEFAULT_LINKEDIN_CSV))
        sym = import_symplicity_xlsx(session, str(DEFAULT_SYMPLICITY_XLSX))
        ref = import_referral_contacts_xlsx(session, str(DEFAULT_REFERRAL_XLSX))
        session.commit()
        _print_import_result("LinkedIn", li)
        _print_import_result("Symplicity", sym)
        _print_import_result("Referrals", ref)
    finally:
        session.close()


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
def list(
    source: str | None = typer.Option(None, "--source"),
    limit: int = 100,
) -> None:
    """List recent job postings."""
    session: Session = SessionLocal()
    try:
        q = session.query(JobPosting).order_by(JobPosting.created_at.desc())
        if source:
            q = q.filter(JobPosting.source_name == source)
        rows = q.limit(limit).all()
        for r in rows:
            company_name = r.company.canonical_name if r.company else "Unknown"
            typer.echo(
                f"{r.id} | {r.source_name or r.source} | {r.title} | {r.status} | {company_name}"
            )
    finally:
        session.close()


@jobs_app.command()
def show(job_id: str, ranked: bool = typer.Option(True, "--ranked/--no-ranked")) -> None:
    """Show details of a specific job posting."""
    session: Session = SessionLocal()
    try:
        if ranked:
            from packages.core.services.job_search_service import job_detail_with_ranking

            try:
                detail = job_detail_with_ranking(session, job_id)
            except LookupError:
                typer.echo("Not found")
                raise typer.Exit(code=1)
            j = detail["job"]
            typer.echo(f"ID: {j.id}")
            typer.echo(f"Title: {j.title}")
            typer.echo(f"Company: {detail['company_name']}")
            typer.echo(f"Source: {j.source_name or j.source} ({j.source_type or '-'})")
            typer.echo(f"Location: {j.location}")
            typer.echo(f"Status: {j.status}")
            typer.echo(f"Rank score: {detail['rank_score']}")
            typer.echo(f"Rank reasons: {', '.join(detail['rank_reasons'])}")
            if detail["is_target_company"]:
                typer.echo(f"Target company: {detail['target_company_name']}")
            if detail["referral_contact_count"]:
                typer.echo(f"Referral contacts: {detail['referral_contact_count']}")
                for c in detail["referral_contacts"][:5]:
                    typer.echo(f"  - {c.contact_name} ({c.position or 'n/a'})")
            if j.company_apply_url:
                typer.echo(f"Apply URL: {j.company_apply_url}")
            if j.canonical_url:
                typer.echo(f"Detail URL: {j.canonical_url}")
            desc_text = j.description_text or ""
            excerpt = desc_text[:500] + ("..." if len(desc_text) > 500 else "")
            typer.echo(f"Description:\n{excerpt}")
            return

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
def queue_cmd(
    limit: int = 20,
    source: str | None = typer.Option(None, "--source", help="Filter by source_name"),
    referral_only: bool = typer.Option(False, "--referral-only"),
    target_only: bool = typer.Option(False, "--target-only"),
    min_score: int | None = typer.Option(None, "--min-score"),
) -> None:
    """List ranked actionable jobs for job search."""
    from packages.core.services.job_search_service import build_ranked_queue

    session: Session = SessionLocal()
    try:
        items = build_ranked_queue(
            session,
            limit=limit,
            source_name=source,
            referral_only=referral_only,
            target_only=target_only,
            min_score=min_score,
        )
        if not items:
            typer.echo("Actionable queue is empty.")
            return
        for item in items:
            flags = []
            if item.is_target_company:
                flags.append("target")
            if item.has_referral_contacts:
                flags.append(f"referral:{item.referral_contact_count}")
            flag_txt = f" [{' '.join(flags)}]" if flags else ""
            typer.echo(
                f"{item.ranked.score:>4} | {item.job.id} | {item.source_name or '-'} | "
                f"{item.company_name} | {item.job.title[:50]}{flag_txt}"
            )
            typer.echo(f"      reasons: {', '.join(item.ranked.reasons[:4])}")
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
