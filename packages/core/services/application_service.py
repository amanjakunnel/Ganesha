from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from packages.core.domain.models import AuditEvent, JobApplication, JobFitResult, JobPosting
from packages.core.services.fit_service import TRACKS
from packages.core.services.job_service import JobNotFoundError

APPLICATION_STATUSES = frozenset(
    {"draft", "ready_to_apply", "submitted", "rejected", "interview", "withdrawn", "skipped"}
)

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"ready_to_apply", "skipped", "withdrawn"},
    "ready_to_apply": {"submitted", "skipped", "withdrawn", "draft"},
    "submitted": {"interview", "rejected"},
    "interview": {"rejected", "withdrawn"},
    "rejected": set(),
    "withdrawn": set(),
    "skipped": set(),
}


class ApplicationError(Exception):
    pass


class ApplicationNotFoundError(ApplicationError):
    pass


class InvalidApplicationTransitionError(ApplicationError):
    pass


def create_application_draft(session: Session, job_id: str) -> JobApplication:
    jp = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
    if jp is None:
        raise JobNotFoundError(job_id)

    existing = (
        session.query(JobApplication).filter(JobApplication.job_posting_id == job_id).one_or_none()
    )
    if existing:
        return existing

    fit = session.query(JobFitResult).filter(JobFitResult.job_posting_id == job_id).one_or_none()
    track = None
    if fit and fit.recommended_track in TRACKS:
        track = fit.recommended_track

    app = JobApplication(job_posting_id=job_id, selected_track=track, status="draft")
    session.add(app)
    setattr(jp, "status", "draft_ready")
    ev = AuditEvent(
        entity_type="job_application",
        entity_id=app.id,
        event_type="application_draft_created",
        payload={"job_posting_id": job_id, "track": track},
    )
    session.add(ev)
    session.flush()
    return app


def get_application(session: Session, application_id: str) -> JobApplication:
    app = session.query(JobApplication).filter(JobApplication.id == application_id).one_or_none()
    if app is None:
        raise ApplicationNotFoundError(application_id)
    return app


def get_application_for_job(session: Session, job_id: str) -> JobApplication | None:
    return (
        session.query(JobApplication).filter(JobApplication.job_posting_id == job_id).one_or_none()
    )


def transition_application(
    session: Session,
    application_id: str,
    new_status: str,
    *,
    note: str | None = None,
) -> JobApplication:
    if new_status not in APPLICATION_STATUSES:
        raise InvalidApplicationTransitionError(f"Unknown status: {new_status}")

    app = get_application(session, application_id)
    current = app.status
    allowed = _ALLOWED_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise InvalidApplicationTransitionError(f"Cannot move {current} -> {new_status}")

    app.status = new_status
    if new_status == "submitted":
        app.submitted_at = datetime.now(UTC)
    if note:
        app.notes = note

    jp = session.query(JobPosting).filter(JobPosting.id == app.job_posting_id).one()
    if new_status == "ready_to_apply":
        setattr(jp, "status", "ready_to_apply")
    elif new_status == "submitted":
        setattr(jp, "status", "applied")
    elif new_status in ("rejected", "withdrawn", "skipped"):
        setattr(jp, "status", "closed")
    elif new_status == "interview":
        setattr(jp, "status", "followup")

    ev = AuditEvent(
        entity_type="job_application",
        entity_id=app.id,
        event_type="application_status_changed",
        payload={"from": current, "to": new_status, "note": note},
    )
    session.add(ev)
    session.add(app)
    session.flush()
    return app
