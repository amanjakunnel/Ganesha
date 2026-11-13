"""Ready-to-apply queue: eligible + fit band + verified JD + live + not applied.

Does not auto-prepare, autofill, or submit. Greenhouse is the only adapter
with controlled prepare/fill today; other ATS platforms are counted separately.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session, joinedload

from packages.core.domain.models import JobPosting
from packages.core.services.evidence_fit_service import (
    BAND_RECOMMENDED,
    BAND_STRETCH,
    get_stored_fit,
)
from packages.core.services.job_eligibility import (
    ELIGIBILITY_ACTIONABLE,
    _job_description_verified,
    resolve_job_eligibility,
)

READY_BANDS = frozenset({BAND_RECOMMENDED, BAND_STRETCH})
ADAPTER_PLATFORM = "greenhouse"
KNOWN_PLATFORMS = ("greenhouse", "lever", "ashby", "workday", "other")


@dataclass
class ReadyToApplyItem:
    job_id: str
    company: str
    role: str
    band: str
    fit_score: int | None
    posted_at: str | None
    platform: str
    application_url: str | None
    adapter_supported: bool
    detail_href: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_ats_platform(url: str | None) -> str:
    """Classify ATS from application / canonical URL host."""
    if not url:
        return "other"
    host = urlparse(url).netloc.lower()
    if "greenhouse.io" in host or "greenhouse.com" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "ashbyhq.com" in host:
        return "ashby"
    if "myworkdayjobs.com" in host or "workday.com" in host:
        return "workday"
    return "other"


def is_live_posting(job: JobPosting) -> bool:
    """True when Simplify/board markers do not mark the listing closed."""
    meta = job.intake_metadata or {}
    markers = meta.get("simplify_markers") or {}
    if markers.get("listing_closed"):
        return False
    if meta.get("closed_reason"):
        return False
    return True


def _company_name(job: JobPosting) -> str:
    if job.company and job.company.canonical_name:
        return job.company.canonical_name
    return "Unknown company"


def _application_url(job: JobPosting) -> str | None:
    for candidate in (job.company_apply_url, job.canonical_url):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return None


def _posted_iso(job: JobPosting) -> str | None:
    for value in (job.posted_at, job.scraped_at, job.discovered_at, job.created_at):
        if isinstance(value, datetime):
            return value.isoformat()
    return None


def _has_prior_or_likely_applied(session: Session, job: JobPosting) -> bool:
    try:
        from packages.core.services.importers.simplify_tracker_csv import (
            previously_applied_for_job,
        )

        if previously_applied_for_job(session, job):
            return True
    except Exception:
        pass

    try:
        from packages.core.services.importers.simplify_tracker_csv import (
            canonical_job_url,
        )
        from packages.core.services.likely_applied_service import list_likely_applied

        urls = {
            u
            for u in (
                canonical_job_url(job.company_apply_url),
                canonical_job_url(job.canonical_url),
            )
            if u
        }
        if not urls:
            return False
        for cand in list_likely_applied(session):
            if cand.job_url and cand.job_url in urls:
                return True
    except Exception:
        pass
    return False


def job_matches_ready_base(session: Session, job: JobPosting) -> tuple[bool, str | None]:
    """Shared filters excluding platform/adapter. Returns (ok, band)."""
    elig = resolve_job_eligibility(job)
    if elig.result != ELIGIBILITY_ACTIONABLE:
        return False, None
    if not _job_description_verified(job):
        return False, None
    if not is_live_posting(job):
        return False, None
    fit = get_stored_fit(job) or {}
    band = fit.get("band")
    if band not in READY_BANDS:
        return False, None
    if _has_prior_or_likely_applied(session, job):
        return False, None
    return True, str(band)


def list_ready_to_apply(
    session: Session,
    *,
    adapter_only: bool = True,
) -> list[ReadyToApplyItem]:
    """Jobs ready for one-click Prepare (Greenhouse adapter by default)."""
    jobs = (
        session.query(JobPosting)
        .options(joinedload(JobPosting.company))
        .order_by(
            JobPosting.posted_at.desc().nullslast(),
            JobPosting.scraped_at.desc().nullslast(),
            JobPosting.created_at.desc(),
        )
        .all()
    )
    items: list[ReadyToApplyItem] = []
    for job in jobs:
        ok, band = job_matches_ready_base(session, job)
        if not ok or band is None:
            continue
        url = _application_url(job)
        platform = detect_ats_platform(url)
        if adapter_only and platform != ADAPTER_PLATFORM:
            continue
        fit = get_stored_fit(job) or {}
        score = fit.get("score")
        items.append(
            ReadyToApplyItem(
                job_id=job.id,
                company=_company_name(job),
                role=(job.title or "").strip() or "(untitled)",
                band=band,
                fit_score=int(score) if isinstance(score, (int, float)) else None,
                posted_at=_posted_iso(job),
                platform=platform,
                application_url=url,
                adapter_supported=platform == ADAPTER_PLATFORM,
                detail_href=f"/jobs/{job.id}",
            )
        )
    return items


def platform_counts_ready(session: Session) -> dict[str, int]:
    """Counts by ATS for jobs that pass ready filters (any platform)."""
    counts = {p: 0 for p in KNOWN_PLATFORMS}
    jobs = (
        session.query(JobPosting)
        .options(joinedload(JobPosting.company))
        .all()
    )
    for job in jobs:
        ok, _band = job_matches_ready_base(session, job)
        if not ok:
            continue
        platform = detect_ats_platform(_application_url(job))
        if platform not in counts:
            platform = "other"
        counts[platform] += 1
    return counts


def ready_to_apply_snapshot(session: Session) -> dict[str, Any]:
    """Dashboard payload: Greenhouse-ready queue + platform breakdown."""
    items = list_ready_to_apply(session, adapter_only=True)
    counts = platform_counts_ready(session)
    return {
        "items": [i.to_dict() for i in items],
        "count": len(items),
        "platform_counts": counts,
        "adapter_platform": ADAPTER_PLATFORM,
        "adapter_note": (
            "Prepare/autofill adapter is Greenhouse-only today. "
            "Ashby, Lever, and Workday counts are informational until adapters ship."
        ),
    }
