from __future__ import annotations

from sqlalchemy.orm import Session

from apps.cover_letter_generation.paths import (
    ELIGIBILITY_BLOCK_MSG,
    INCOMPLETE_IDENTITY_MSG,
    INSUFFICIENT_MSG,
    MIN_DESCRIPTION_CHARS,
    MISSING_URL_MSG,
)
from apps.resume_generation.services.resume_fact_service import (
    has_unresolved_identity_conflicts,
)
from packages.core.domain.models import JobPosting
from packages.core.services.job_eligibility import (
    ELIGIBILITY_EXCLUDED,
    resolve_job_eligibility,
)

_CLOSED_STATUSES = {"closed", "skipped", "archived"}


def cover_letter_preflight(session: Session, job: JobPosting) -> str | None:
    """Return a user-safe block reason, or None when generation may continue."""
    if (job.status or "") in _CLOSED_STATUSES:
        return ELIGIBILITY_BLOCK_MSG

    eligibility = resolve_job_eligibility(job)
    if eligibility.result == ELIGIBILITY_EXCLUDED:
        return ELIGIBILITY_BLOCK_MSG

    company = job.company.canonical_name if job.company else ""
    title = (job.title or "").strip()
    if not title or not company.strip() or company.strip().lower() == "unknown":
        return INCOMPLETE_IDENTITY_MSG

    apply_url = (job.company_apply_url or job.canonical_url or "").strip()
    if not apply_url:
        return MISSING_URL_MSG

    description = (job.description_text or "").strip()
    if len(description) < MIN_DESCRIPTION_CHARS:
        return INSUFFICIENT_MSG

    identity = has_unresolved_identity_conflicts(session)
    if identity:
        return identity[0]
    return None
