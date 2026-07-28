from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from packages.core.domain.models import (
    Company,
    DecisionRequest,
    JobApplication,
    JobAssessment,
    JobFitResult,
    JobPosting,
    ReferralTask,
    make_dedupe_key,
    make_description_hash,
)
from packages.core.services import application_service, decision_service, fit_service
from packages.core.services.job_service import JobNotFoundError, assess_job, start_referral

DEMO_COMPANY = "Ganesha Demo Co [workflow]"
DEMO_JOB_TITLE = "Backend Engineer — Python APIs (workflow demo)"
DEMO_LOCATION = "Remote"

LIFECYCLE_VALUES = (
    "discovered",
    "assessed",
    "needs_decision",
    "draft_ready",
    "ready_to_apply",
    "applied",
    "followup",
    "closed",
)


@dataclass
class ActionableItem:
    job_id: str
    title: str
    company: str
    lifecycle: str
    reason: str


def compute_job_lifecycle(session: Session, job_id: str) -> str:
    jp = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
    if jp is None:
        raise JobNotFoundError(job_id)

    app = (
        session.query(JobApplication).filter(JobApplication.job_posting_id == job_id).one_or_none()
    )
    if app:
        if app.status == "submitted":
            return "applied"
        if app.status == "interview":
            return "followup"
        if app.status in ("rejected", "withdrawn", "skipped"):
            return "closed"
        if app.status == "ready_to_apply":
            return "ready_to_apply"
        if app.status == "draft":
            return "draft_ready"

    pending = decision_service.list_pending_decisions(
        session, entity_type="job_posting", entity_id=job_id
    )
    if pending:
        return "needs_decision"

    assessment = (
        session.query(JobAssessment).filter(JobAssessment.job_posting_id == job_id).one_or_none()
    )
    if assessment:
        fit = session.query(JobFitResult).filter(JobFitResult.job_posting_id == job_id).one_or_none()
        if fit and fit.readiness_status == "needs_decision":
            return "needs_decision"
        return "assessed" if jp.status in ("new", "queued_for_review", "assessed") else _map_job_status(jp.status)

    if jp.status in ("skipped", "archived", "deferred"):
        return "closed"
    return "discovered"


def _map_job_status(status: str) -> str:
    mapping = {
        "new": "discovered",
        "queued_for_review": "assessed",
        "needs_decision": "needs_decision",
        "draft_ready": "draft_ready",
        "ready_to_apply": "ready_to_apply",
        "approved": "draft_ready",
        "applied_manually": "applied",
        "skipped": "closed",
        "archived": "closed",
        "deferred": "closed",
        "assessed": "assessed",
        "applied": "applied",
        "followup": "followup",
        "closed": "closed",
    }
    return mapping.get(status, "discovered")


def list_actionable_queue(session: Session, limit: int = 50) -> list[ActionableItem]:
    """Jobs that need operator attention (search + fit + decisions)."""
    items: list[ActionableItem] = []

    for jp in (
        session.query(JobPosting)
        .filter(JobPosting.status.in_(["new", "queued_for_review", "needs_decision", "draft_ready", "ready_to_apply"]))
        .order_by(JobPosting.updated_at.desc())
        .limit(limit)
        .all()
    ):
        company = jp.company.canonical_name if jp.company else "Unknown"
        lifecycle = compute_job_lifecycle(session, jp.id)
        reason = f"status={jp.status}"
        items.append(
            ActionableItem(job_id=jp.id, title=jp.title, company=company, lifecycle=lifecycle, reason=reason)
        )

    for d in decision_service.list_pending_decisions(session):
        if d.entity_type != "job_posting" or not d.entity_id:
            continue
        job_row = session.query(JobPosting).filter(JobPosting.id == d.entity_id).one_or_none()
        if job_row is None:
            continue
        if any(i.job_id == job_row.id for i in items):
            continue
        company = job_row.company.canonical_name if job_row.company else "Unknown"
        items.append(
            ActionableItem(
                job_id=job_row.id,
                title=job_row.title,
                company=company,
                lifecycle="needs_decision",
                reason=f"decision:{d.decision_type}",
            )
        )

    return items[:limit]


def dashboard_summary(session: Session) -> dict[str, Any]:
    jobs_total = session.query(JobPosting).count()
    pending_decisions = len(decision_service.list_pending_decisions(session))
    applications = session.query(JobApplication).count()
    ready = session.query(JobApplication).filter(JobApplication.status == "ready_to_apply").count()
    submitted = session.query(JobApplication).filter(JobApplication.status == "submitted").count()
    queue = list_actionable_queue(session, limit=10)
    return {
        "jobs_total": jobs_total,
        "pending_decisions": pending_decisions,
        "applications_total": applications,
        "applications_ready": ready,
        "applications_submitted": submitted,
        "actionable_queue": [
            {
                "job_id": i.job_id,
                "title": i.title,
                "company": i.company,
                "lifecycle": i.lifecycle,
                "reason": i.reason,
            }
            for i in queue
        ],
    }


def apply_decision_effects(session: Session, decision: DecisionRequest) -> None:
    """Update domain entities after a decision is resolved."""
    if decision.decision_type == "referral_window" and decision.entity_id:
        rt = None
        jp = None
        if decision.entity_type == "referral_task":
            rt = session.query(ReferralTask).filter(ReferralTask.id == decision.entity_id).one_or_none()
            if rt:
                jp = session.query(JobPosting).filter(JobPosting.id == rt.job_posting_id).one_or_none()
        elif decision.entity_type == "job_posting":
            jp = session.query(JobPosting).filter(JobPosting.id == decision.entity_id).one_or_none()
            if jp:
                rt = (
                    session.query(ReferralTask)
                    .filter(ReferralTask.job_posting_id == jp.id)
                    .one_or_none()
                )

        action = decision.selected_action or ""
        if rt:
            if action == "request_referral":
                rt.status = "research_needed"
            elif action == "apply_now":
                rt.status = "closed"
                if jp:
                    setattr(jp, "status", "ready_to_apply")
            elif action == "wait_until_cutoff":
                rt.status = "waiting"
            elif action == "skip":
                rt.status = "closed"
                if jp:
                    setattr(jp, "status", "skipped")
            elif action in ("start_referral", "skip_referral"):
                rt.status = "research_needed" if action == "start_referral" else "closed"
            session.add(rt)
        if jp:
            session.add(jp)

    if decision.decision_type == "track_selection" and decision.entity_id:
        jp = session.query(JobPosting).filter(JobPosting.id == decision.entity_id).one_or_none()
        if jp and decision.selected_action:
            track_map = {
                "track_ml": "ml",
                "track_cloud": "cloud",
                "track_dev": "dev",
            }
            if decision.selected_action in track_map:
                fit = (
                    session.query(JobFitResult)
                    .filter(JobFitResult.job_posting_id == jp.id)
                    .one_or_none()
                )
                if fit:
                    fit.recommended_track = track_map[decision.selected_action]
                    fit.readiness_status = "ready"
                    fit.next_action = "create_application_draft"
                    session.add(fit)
                setattr(jp, "status", "draft_ready")
                session.add(jp)
            elif decision.selected_action == "defer_review":
                setattr(jp, "status", "deferred")
                session.add(jp)


def resolve_decision_with_effects(
    session: Session,
    *,
    decision_id: str,
    actor: str,
    selected_action: str,
    note: str | None = None,
) -> DecisionRequest:
    d = decision_service.resolve_decision_request(
        session,
        decision_id=decision_id,
        actor=actor,
        selected_action=selected_action,
        note=note,
    )
    apply_decision_effects(session, d)
    return d


def ensure_demo_job(session: Session) -> JobPosting:
    company = (
        session.query(Company).filter(Company.canonical_name == DEMO_COMPANY).one_or_none()
    )
    if not company:
        company = Company(canonical_name=DEMO_COMPANY, website_domain="ganesha-demo.example")
        session.add(company)
        session.flush()

    dedupe = make_dedupe_key(DEMO_COMPANY, DEMO_JOB_TITLE, DEMO_LOCATION)
    desc = (
        "We are hiring a backend engineer to build Python APIs and services on AWS. "
        "Requirements:\n"
        "- 3+ years Python backend development\n"
        "- Experience designing REST APIs\n"
        "- Familiarity with Docker and cloud deployment\n"
    )
    job = session.query(JobPosting).filter(JobPosting.dedupe_key == dedupe).one_or_none()
    if not job:
        job = JobPosting(
            source="manual",
            title=DEMO_JOB_TITLE,
            company_id=company.id,
            location=DEMO_LOCATION,
            description_text=desc,
            description_hash=make_description_hash(desc),
            dedupe_key=dedupe,
            status="new",
        )
        session.add(job)
        session.flush()
    return job


def run_demo_loop(session: Session) -> dict[str, str]:
    """End-to-end demo across job search, fit, referral decision, and application."""
    job = ensure_demo_job(session)
    assess_job(session, job.id)
    fit = fit_service.build_fit_result(session, job.id, run_assessment=False)
    rt = start_referral(session, job.id, cutoff_hours=48)
    app = application_service.create_application_draft(session, job.id)
    if app.status == "draft":
        application_service.transition_application(session, app.id, "ready_to_apply")

    dec = (
        session.query(DecisionRequest)
        .filter(DecisionRequest.idempotency_key == f"referral:{rt.id}")
        .one_or_none()
    )

    lifecycle = compute_job_lifecycle(session, job.id)
    return {
        "job_id": job.id,
        "fit_id": fit.id,
        "referral_task_id": rt.id,
        "application_id": app.id,
        "decision_id": dec.id if dec else "",
        "lifecycle": lifecycle,
        "application_status": app.status,
        "fit_readiness": fit.readiness_status,
        "fit_track": fit.recommended_track,
    }


def format_job_summary(session: Session, job_id: str) -> str:
    jp = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
    if jp is None:
        raise JobNotFoundError(job_id)
    company = jp.company.canonical_name if jp.company else "Unknown"
    lifecycle = compute_job_lifecycle(session, job_id)
    fit = fit_service.get_fit_result(session, job_id)
    app = application_service.get_application_for_job(session, job_id)
    ref = session.query(ReferralTask).filter(ReferralTask.job_posting_id == job_id).one_or_none()
    lines = [
        f"Job: {jp.title}",
        f"Company: {company}",
        f"Status: {jp.status}",
        f"Lifecycle: {lifecycle}",
    ]
    if fit:
        lines.append(f"Fit track: {fit.recommended_track} ({fit.readiness_status})")
        lines.append(f"Next action: {fit.next_action}")
    if ref:
        lines.append(f"Referral: {ref.status} cutoff={ref.cutoff_at}")
    if app:
        lines.append(f"Application: {app.id} status={app.status}")
    pending = decision_service.list_pending_decisions(
        session, entity_type="job_posting", entity_id=job_id
    )
    if pending:
        lines.append(f"Pending decisions: {len(pending)}")
    return "\n".join(lines)


def format_application_summary(session: Session, application_id: str) -> str:
    app = application_service.get_application(session, application_id)
    return format_job_summary(session, app.job_posting_id) + f"\nApplication ID: {app.id}"
