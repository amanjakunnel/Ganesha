"""Pre-apply liveness checks for a single job (or small on-demand set)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from packages.core.domain.models import JobPosting
from packages.core.services.job_eligibility import (
    attach_eligibility_to_metadata,
    classify_job_eligibility,
)


@dataclass
class LivenessResult:
    job_id: str
    url: str | None
    live: bool
    reason: str
    marked_closed: bool = False
    status_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEAD_MARKERS = (
    "job not found",
    "no longer accepting applications",
    "this job is closed",
    "this position has been filled",
    "page not found",
    "404",
    "greenhouse job board — job not found",
)


def check_job_liveness(
    session: Session,
    job: JobPosting,
    *,
    timeout: float = 15.0,
    mark_closed: bool = True,
) -> LivenessResult:
    url = (job.company_apply_url or job.canonical_url or "").strip() or None
    if not url:
        return LivenessResult(job.id, None, False, "missing_application_url")
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        return LivenessResult(
            job.id, url, False, f"request_failed:{exc.__class__.__name__}"
        )

    body = (response.text or "")[:8000].lower()
    dead = response.status_code >= 400 or any(m in body for m in DEAD_MARKERS)
    if not dead:
        return LivenessResult(
            job.id, url, True, "live", status_code=response.status_code
        )

    marked = False
    if mark_closed:
        meta = dict(job.intake_metadata or {})
        markers = dict(meta.get("simplify_markers") or {})
        markers["listing_closed"] = True
        meta["simplify_markers"] = markers
        meta["closed_reason"] = "liveness_check_dead"
        meta["closed_at"] = datetime.now(UTC).isoformat()
        eligibility = classify_job_eligibility(
            title=job.title,
            description=job.description_text or "",
            company_apply_url=job.company_apply_url,
            location=job.location,
            employment_type=job.employment_type,
            source_markers=markers,
        )
        job.intake_metadata = attach_eligibility_to_metadata(meta, eligibility)
        session.add(job)
        session.flush()
        marked = True
    return LivenessResult(
        job.id,
        url,
        False,
        "dead_posting",
        marked_closed=marked,
        status_code=response.status_code,
    )
