from __future__ import annotations

from sqlalchemy.orm import Session

from apps.resume_generation.services.job_requirement_service import (
    extract_job_requirements,
)
from apps.resume_generation.services.resume_fact_service import (
    has_unresolved_identity_conflicts,
)
from packages.core.domain.models import JobPosting
from packages.core.services.job_eligibility import (
    ELIGIBILITY_EXCLUDED,
    resolve_job_eligibility,
)

ELIGIBILITY_BLOCK_MSG = (
    "Resume generation is unavailable because this job does not meet your "
    "configured eligibility policy."
)

INSUFFICIENT_MSG = (
    "Cannot generate a reliable tailored resume because this job does not "
    "contain enough job-description detail."
)

MISSING_URL_MSG = (
    "Resume generation blocked: this job is missing a valid application URL."
)


def generation_preflight(session: Session, job: JobPosting) -> str | None:
    """Return a concrete block reason, or None if generation may proceed."""
    eligibility = resolve_job_eligibility(job)
    if eligibility.result == ELIGIBILITY_EXCLUDED:
        return ELIGIBILITY_BLOCK_MSG

    apply_url = (job.company_apply_url or job.canonical_url or "").strip()
    if not apply_url:
        return MISSING_URL_MSG

    req = extract_job_requirements(job)
    if req.insufficient_description and len((job.description_text or "").strip()) < 40:
        return INSUFFICIENT_MSG

    identity = has_unresolved_identity_conflicts(session)
    if identity:
        return identity[0]

    return None
