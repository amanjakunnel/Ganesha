from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from packages.core.domain.models import AuditEvent, JobAssessment, JobFitResult, JobPosting
from packages.core.services import decision_service
from packages.core.services.job_service import JobNotFoundError, assess_job

TRACKS = ("ml", "cloud", "dev")
_REQUIREMENT_LINE = re.compile(r"^[\s•\-*]+(.+)$", re.MULTILINE)


def _extract_key_requirements(description: str, limit: int = 8) -> list[str]:
    """Extract requirement-like lines from a posting without inventing content."""
    text = description or ""
    found: list[str] = []
    for match in _REQUIREMENT_LINE.finditer(text):
        line = match.group(1).strip()
        if 10 <= len(line) <= 200:
            found.append(line)
    if found:
        return found[:limit]
    # Fallback: first non-empty sentences from description
    sentences = [s.strip() for s in re.split(r"[.\n]", text) if len(s.strip()) >= 20]
    return sentences[: min(limit, len(sentences))]


def _missing_evidence_from_assessment(assessment: JobAssessment) -> list[str]:
    gaps = list(assessment.missing_or_uncertain_skills or [])
    reasons = list(assessment.manual_review_reasons or [])
    combined: list[str] = []
    for item in gaps + reasons:
        if item and item not in combined:
            combined.append(item)
    return combined


def build_fit_result(session: Session, job_id: str, *, run_assessment: bool = True) -> JobFitResult:
    """Create or refresh a durable fit package for a job."""
    jp = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
    if jp is None:
        raise JobNotFoundError(job_id)

    assessment = (
        session.query(JobAssessment).filter(JobAssessment.job_posting_id == job_id).one_or_none()
    )
    if assessment is None and run_assessment:
        assessment = assess_job(session, job_id)
    if assessment is None:
        raise ValueError("Job has no assessment; run jobs assess first")

    track = assessment.recommended_track
    key_requirements = _extract_key_requirements(jp.description_text or "")
    missing = _missing_evidence_from_assessment(assessment)

    readiness = "ready"
    next_action = "create_application_draft"
    decision_id: str | None = None

    if track == "manual_review" or track not in TRACKS:
        readiness = "needs_decision"
        next_action = "resolve_track_selection"
        dr = decision_service.create_decision_request(
            session,
            entity_type="job_posting",
            entity_id=jp.id,
            decision_type="track_selection",
            reason_code="ambiguous_track",
            summary="Choose resume track (ml, cloud, or dev) for this job",
            options=["track_ml", "track_cloud", "track_dev", "defer_review"],
            default_action="defer_review",
            idempotency_key=f"fit-track:{jp.id}",
        )
        decision_id = dr.id
        setattr(jp, "status", "needs_decision")
    elif missing:
        readiness = "needs_decision"
        next_action = "review_evidence_gaps"
        decision_service.create_decision_request(
            session,
            entity_type="job_posting",
            entity_id=jp.id,
            decision_type="evidence_gap",
            reason_code="uncertain_evidence",
            summary="Review evidence gaps before drafting application materials",
            options=["proceed_with_gaps", "defer_review", "skip_job"],
            default_action="defer_review",
            idempotency_key=f"fit-evidence:{jp.id}",
        )
        setattr(jp, "status", "needs_decision")
    else:
        setattr(jp, "status", "draft_ready")

    existing = (
        session.query(JobFitResult).filter(JobFitResult.job_posting_id == job_id).one_or_none()
    )
    payload: dict[str, Any] = {
        "recommended_track": track if track in TRACKS else "ambiguous",
        "key_requirements": key_requirements,
        "missing_evidence": missing,
        "readiness_status": readiness,
        "next_action": next_action,
    }
    if decision_id:
        payload["track_decision_id"] = decision_id

    if existing:
        existing.recommended_track = payload["recommended_track"]
        existing.key_requirements = key_requirements
        existing.missing_evidence = missing
        existing.readiness_status = readiness
        existing.next_action = next_action
        session.add(existing)
        fit = existing
    else:
        fit = JobFitResult(
            job_posting_id=jp.id,
            recommended_track=payload["recommended_track"],
            key_requirements=key_requirements,
            missing_evidence=missing,
            readiness_status=readiness,
            next_action=next_action,
        )
        session.add(fit)

    ev = AuditEvent(
        entity_type="job_posting",
        entity_id=jp.id,
        event_type="fit_built",
        payload={"readiness": readiness, "track": fit.recommended_track},
    )
    session.add(ev)
    session.flush()
    return fit


def get_fit_result(session: Session, job_id: str) -> JobFitResult | None:
    return session.query(JobFitResult).filter(JobFitResult.job_posting_id == job_id).one_or_none()
