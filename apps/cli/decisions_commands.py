from datetime import datetime, timedelta
import typer
from sqlalchemy.orm import Session

from packages.core.db import SessionLocal
from packages.core.services import decision_service
from packages.core.domain.models import DecisionRequest, Company, JobPosting, ReferralTask, make_dedupe_key, make_description_hash


decisions_app = typer.Typer(help="Decision request commands")


@decisions_app.command("list")
def list_decisions(all: bool = typer.Option(False, "--all", help="Include non-pending decisions"), decision_type: str | None = None) -> None:
    session: Session = SessionLocal()
    try:
        if all:
            rows = session.query(DecisionRequest).order_by(DecisionRequest.created_at.desc()).limit(200).all()
        else:
            rows = decision_service.list_pending_decisions(session, decision_type=decision_type)
        for r in rows:
            expires = r.expires_at.isoformat() if r.expires_at else "-"
            typer.echo(f"{r.id} | {r.decision_type} | {r.entity_type}:{r.entity_id} | expires={expires} | default={r.default_action} | {r.summary}")
    finally:
        session.close()


@decisions_app.command("show")
def show_decision(decision_id: str) -> None:
    session: Session = SessionLocal()
    try:
        d = decision_service.get_decision_request(session, decision_id)
        typer.echo(f"ID: {d.id}")
        typer.echo(f"Type: {d.decision_type}")
        typer.echo(f"Entity: {d.entity_type}:{d.entity_id}")
        typer.echo(f"Status: {d.status}")
        typer.echo(f"Summary: {d.summary}")
        typer.echo(f"Options: {d.options_json}")
        typer.echo(f"Default: {d.default_action}")
        typer.echo(f"Expires at: {d.expires_at}")
        if d.status != "pending":
            typer.echo(f"Resolved at: {d.resolved_at} by {d.resolved_by}")
            typer.echo(f"Selected action: {d.selected_action}")
            typer.echo(f"Resolution note: {d.resolution_note}")
    except Exception:
        typer.echo("Not found")
        raise typer.Exit(code=1)
    finally:
        session.close()


@decisions_app.command("resolve")
def resolve_decision(decision_id: str, action: str, actor: str = typer.Option("cli", "--actor"), note: str | None = typer.Option(None, "--note")) -> None:
    session: Session = SessionLocal()
    try:
        try:
            d = decision_service.resolve_decision_request(session, decision_id=decision_id, actor=actor, selected_action=action, note=note)
            session.commit()
            typer.echo(f"Resolved: {d.id} -> {d.selected_action} by {d.resolved_by}")
        except decision_service.DecisionNotFoundError:
            typer.echo("Decision not found")
            raise typer.Exit(code=1)
        except decision_service.InvalidActionError as exc:
            typer.echo(f"Invalid action: {exc}")
            raise typer.Exit(code=2)
        except decision_service.InvalidTransitionError as exc:
            typer.echo(f"Invalid transition: {exc}")
            raise typer.Exit(code=3)
    finally:
        session.close()


@decisions_app.command("expire-due")
def expire_due() -> None:
    session: Session = SessionLocal()
    try:
        count = decision_service.expire_due_decision_requests(session)
        session.commit()
        typer.echo(f"Expired: {count}")
    finally:
        session.close()


@decisions_app.command("demo-referral")
def demo_referral() -> None:
    """Create or reuse a demo referral task and pending decision request."""
    session: Session = SessionLocal()
    try:
        # 1. Company
        company_name = "Demo Corp (Demo Tag)"
        company = session.query(Company).filter(Company.canonical_name == company_name).one_or_none()
        if not company:
            company = Company(canonical_name=company_name, website_domain="democorp.example.com")
            session.add(company)
            session.flush()

        # 2. JobPosting
        job_title = "Software Engineer (Demo Tag)"
        location = "Remote"
        dedupe = make_dedupe_key(company_name, job_title, location)
        desc_text = "Demo job description for testing the Telegram decision flow."
        desc_hash = make_description_hash(desc_text)

        job = session.query(JobPosting).filter(JobPosting.dedupe_key == dedupe).one_or_none()
        if not job:
            job = JobPosting(
                source="manual",
                title=job_title,
                company_id=company.id,
                location=location,
                description_text=desc_text,
                description_hash=desc_hash,
                dedupe_key=dedupe,
                status="new",
            )
            session.add(job)
            session.flush()

        # 3. ReferralTask
        ref_task = session.query(ReferralTask).filter(ReferralTask.job_posting_id == job.id).one_or_none()
        if not ref_task:
            ref_task = ReferralTask(
                job_posting_id=job.id,
                status="draft_ready",
                cutoff_at=datetime.utcnow() + timedelta(hours=48),
            )
            session.add(ref_task)
            session.flush()

        # 4. DecisionRequest
        idem_key = f"demo-referral-decision-{job.id}"

        d = decision_service.create_decision_request(
            session,
            entity_type="referral_task",
            entity_id=ref_task.id,
            decision_type="referral_window",
            reason_code="referral_needed",
            summary="Demo Referral Window Decision Request",
            options=["start_referral", "skip_referral"],
            default_action="start_referral",
            expires_at=datetime.utcnow() + timedelta(hours=48),
            idempotency_key=idem_key,
        )

        if d.status != "pending":
            # Reset to pending for easy replay
            d.status = "pending"
            d.resolved_at = None
            d.resolved_by = None
            d.selected_action = None
            d.resolution_note = None
            d.expires_at = datetime.utcnow() + timedelta(hours=48)
            session.add(d)
            session.flush()

        session.commit()

        typer.echo(f"Job ID: {job.id}")
        typer.echo(f"Referral Task ID: {ref_task.id}")
        typer.echo(f"Decision ID: {d.id}")
        typer.echo(f"Decision Status: {d.status}")
        typer.echo("")
        typer.echo("Follow-up Commands:")
        typer.echo(f"  Show:    .venv/bin/python -m apps.cli.main decisions show {d.id}")
        typer.echo(f"  List:    .venv/bin/python -m apps.cli.main decisions list")
        typer.echo(f"  Resolve: .venv/bin/python -m apps.cli.main decisions resolve {d.id} start_referral")

    except Exception as exc:
        session.rollback()
        typer.secho(f"Error creating demo: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    finally:
        session.close()


@decisions_app.command("demo-cleanup")
def demo_cleanup(
    yes: bool = typer.Option(False, "--yes", help="Confirm deletion of demo-marked records.")
) -> None:
    """Safely drop all demo-tagged records (Company, Job, ReferralTask, DecisionRequest) from the database."""
    if not yes:
        typer.echo("Please run with --yes to confirm deletion of demo records.")
        raise typer.Exit(code=1)

    session: Session = SessionLocal()
    try:
        # Find demo company
        company_name = "Demo Corp (Demo Tag)"
        company = session.query(Company).filter(Company.canonical_name == company_name).one_or_none()
        if company:
            # Find related jobs
            jobs = session.query(JobPosting).filter(JobPosting.company_id == company.id).all()
            job_ids = [j.id for j in jobs]
            if job_ids:
                # Find referral tasks
                ref_tasks = session.query(ReferralTask).filter(ReferralTask.job_posting_id.in_(job_ids)).all()
                ref_task_ids = [r.id for r in ref_tasks]

                # Delete related DecisionRequests
                if ref_task_ids:
                    session.query(DecisionRequest).filter(
                        DecisionRequest.entity_type == "referral_task",
                        DecisionRequest.entity_id.in_(ref_task_ids)
                    ).delete(synchronize_session=False)

                    # Delete referral tasks
                    for rt in ref_tasks:
                        session.delete(rt)

                # Delete job postings
                for j in jobs:
                    session.delete(j)

            # Delete company
            session.delete(company)

        session.commit()
        typer.secho("Demo records cleaned up successfully.", fg=typer.colors.GREEN)
    except Exception as exc:
        session.rollback()
        typer.secho(f"Error during cleanup: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    finally:
        session.close()
