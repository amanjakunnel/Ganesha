"""Ready-to-apply queue: actionable, fit-banded, verified, unapplied, live jobs.

Greenhouse-only list for the working autofill path; platform counts cover all
otherwise-ready jobs (Ashby / Lever / Workday / other).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session, joinedload

from packages.core.domain.models import JobApplication, JobPosting, SimplifyHistoryRecord
from packages.core.services.evidence_fit_service import (
    BAND_RECOMMENDED,
    BAND_STRETCH,
    get_stored_fit,
)
from packages.core.services.importers.simplify_tracker_csv import (
    canonical_job_url,
    previously_applied_for_job,
)
from packages.core.services.job_eligibility import (
    ELIGIBILITY_ACTIONABLE,
    resolve_job_eligibility,
)
from packages.core.services.job_enrichment import description_verified
from packages.core.services.role_profile import (
    STATUS_MISMATCH,
    get_stored_role_block,
    is_role_family_mismatch,
)
from packages.core.services.status_precedence import is_at_least

PLATFORM_GREENHOUSE = "greenhouse"
PLATFORM_LEVER = "lever"
PLATFORM_ASHBY = "ashby"
PLATFORM_WORKDAY = "workday"
PLATFORM_OTHER = "other"

READY_FIT_BANDS = frozenset({BAND_RECOMMENDED, BAND_STRETCH})
QUEUE_PLATFORM = PLATFORM_GREENHOUSE


@dataclass
class ReadyJob:
    id: str
    company: str
    role: str
    band: str
    posted_at: str | None
    platform: str
    job_url: str | None
    prepare_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReadyToApplyPayload:
    jobs: list[ReadyJob] = field(default_factory=list)
    counts_by_platform: dict[str, int] = field(default_factory=dict)
    greenhouse_ready: int = 0
    recommendation: str | None = None
    total_otherwise_ready: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "jobs": [j.to_dict() for j in self.jobs],
            "counts_by_platform": dict(self.counts_by_platform),
            "greenhouse_ready": self.greenhouse_ready,
            "recommendation": self.recommendation,
            "total_otherwise_ready": self.total_otherwise_ready,
        }


def detect_ats_platform(job: JobPosting) -> str:
    """Infer ATS platform from source_name or apply/canonical URL host."""
    source = (job.source_name or "").strip().lower()
    if source in {
        PLATFORM_GREENHOUSE,
        PLATFORM_LEVER,
        PLATFORM_ASHBY,
        PLATFORM_WORKDAY,
    }:
        return source

    meta = job.intake_metadata or {}
    board = meta.get("board") or meta.get("company_board") or {}
    if isinstance(board, dict):
        plat = str(board.get("platform") or "").strip().lower()
        if plat in {
            PLATFORM_GREENHOUSE,
            PLATFORM_LEVER,
            PLATFORM_ASHBY,
            PLATFORM_WORKDAY,
        }:
            return plat

    for candidate in (job.company_apply_url, job.canonical_url):
        host = _host(candidate)
        if not host:
            continue
        if "greenhouse.io" in host:
            return PLATFORM_GREENHOUSE
        if "lever.co" in host:
            return PLATFORM_LEVER
        if "ashbyhq.com" in host:
            return PLATFORM_ASHBY
        if "myworkdayjobs.com" in host or "workday.com" in host:
            return PLATFORM_WORKDAY
    return PLATFORM_OTHER


def _host(url: str | None) -> str:
    if not url:
        return ""
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _company_name(job: JobPosting) -> str:
    if job.company is not None and getattr(job.company, "canonical_name", None):
        return str(job.company.canonical_name)
    meta = job.intake_metadata or {}
    for key in ("company", "company_name", "employer"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return "Unknown company"


def _posted_iso(job: JobPosting) -> str | None:
    for ts in (job.posted_at, job.scraped_at, job.discovered_at, job.created_at):
        if ts is None:
            continue
        if isinstance(ts, datetime):
            return ts.isoformat()
    return None


def _sort_key(job: JobPosting) -> datetime:
    for ts in (job.posted_at, job.scraped_at, job.discovered_at, job.created_at):
        if isinstance(ts, datetime):
            return ts
    return datetime.min


def is_live_posting(job: JobPosting) -> bool:
    """Prefer stored liveness / closed markers; else status not closed."""
    if (job.status or "").lower() in {"closed", "archived", "skipped"}:
        return False
    meta = job.intake_metadata or {}
    markers = meta.get("simplify_markers") or {}
    if markers.get("listing_closed"):
        return False
    if meta.get("closed_reason"):
        return False
    liveness = meta.get("liveness") or meta.get("job_liveness") or {}
    if isinstance(liveness, dict) and "live" in liveness:
        return bool(liveness.get("live"))
    return True


def _preload_applied_indexes(session: Session) -> tuple[set[str], set[str]]:
    """Load applied job ids and URLs once (avoid O(n×history) per candidate)."""
    applied_ids: set[str] = set()
    applied_urls: set[str] = set()
    try:
        apps = (
            session.query(JobApplication)
            .filter(
                JobApplication.status.in_(
                    ["submitted", "interview", "rejected", "withdrawn", "offer"]
                )
            )
            .all()
        )
        hist = session.query(SimplifyHistoryRecord).all()
    except Exception:
        # Unit tests often pass MagicMock sessions without ORM chaining.
        return applied_ids, applied_urls

    for app in apps or []:
        if getattr(app, "job_posting_id", None):
            applied_ids.add(str(app.job_posting_id))

    for row in hist or []:
        status = getattr(row, "status_mapped", None)
        payload = getattr(row, "payload_json", None) or {}
        likely = isinstance(payload, dict) and payload.get("likely_applied_confirmed")
        appliedish = bool(
            status == "applied" or is_at_least(status or "", "applied") or likely
        )
        if not appliedish:
            continue
        if getattr(row, "job_posting_id", None):
            applied_ids.add(str(row.job_posting_id))
        job_url = getattr(row, "job_url", None)
        if job_url:
            applied_urls.add(canonical_job_url(job_url) or job_url)
    return applied_ids, applied_urls


def is_previously_or_likely_applied(
    session: Session,
    job: JobPosting,
    *,
    applied_ids: set[str] | None = None,
    applied_urls: set[str] | None = None,
) -> bool:
    """True when history / applications / status say already or likely applied."""
    if (job.status or "").lower() in {"applied_manually", "applied"}:
        return True

    if applied_ids is not None and applied_urls is not None:
        if str(job.id) in applied_ids:
            return True
        for candidate in (
            canonical_job_url(job.company_apply_url),
            canonical_job_url(job.canonical_url),
        ):
            if candidate and candidate in applied_urls:
                return True
        return False

    # Fallback path (tests / single-job checks).
    if previously_applied_for_job(session, job) is not None:
        return True

    app = (
        session.query(JobApplication)
        .filter(
            JobApplication.job_posting_id == job.id,
            JobApplication.status.in_(
                ["submitted", "interview", "rejected", "withdrawn", "offer"]
            ),
        )
        .first()
    )
    if app is not None:
        return True

    rows = (
        session.query(SimplifyHistoryRecord)
        .filter(SimplifyHistoryRecord.job_posting_id == job.id)
        .all()
    )
    for row in rows:
        if is_at_least(row.status_mapped, "applied"):
            return True
        payload = row.payload_json or {}
        if isinstance(payload, dict) and payload.get("likely_applied_confirmed"):
            return True
    return False


def job_is_otherwise_ready(
    session: Session,
    job: JobPosting,
    *,
    applied_ids: set[str] | None = None,
    applied_urls: set[str] | None = None,
) -> bool:
    """Shared readiness rules excluding Greenhouse-only queue filter."""
    eligibility = resolve_job_eligibility(job)
    if eligibility.result != ELIGIBILITY_ACTIONABLE:
        return False

    fit = get_stored_fit(job) or {}
    if fit.get("band") not in READY_FIT_BANDS:
        return False

    if is_role_family_mismatch(job):
        return False
    block = get_stored_role_block(job) or {}
    if block.get("status") == STATUS_MISMATCH:
        return False

    if not description_verified(job):
        return False

    if not is_live_posting(job):
        return False

    if is_previously_or_likely_applied(
        session, job, applied_ids=applied_ids, applied_urls=applied_urls
    ):
        return False

    return True


def list_ready_to_apply(
    session: Session,
    *,
    queue_platform: str = QUEUE_PLATFORM,
    limit: int = 500,
) -> ReadyToApplyPayload:
    """Return Greenhouse queue rows + platform counts for all otherwise-ready."""
    jobs = (
        session.query(JobPosting)
        .options(joinedload(JobPosting.company))
        .order_by(JobPosting.created_at.desc())
        .limit(8000)
        .all()
    )
    applied_ids, applied_urls = _preload_applied_indexes(session)

    otherwise: list[tuple[JobPosting, str]] = []
    for job in jobs:
        if not job_is_otherwise_ready(
            session, job, applied_ids=applied_ids, applied_urls=applied_urls
        ):
            continue
        platform = detect_ats_platform(job)
        otherwise.append((job, platform))

    counts: dict[str, int] = {}
    for _job, platform in otherwise:
        counts[platform] = counts.get(platform, 0) + 1

    greenhouse = [
        (job, plat) for job, plat in otherwise if plat == queue_platform
    ]
    greenhouse.sort(key=lambda pair: _sort_key(pair[0]), reverse=True)

    ready_jobs: list[ReadyJob] = []
    for job, plat in greenhouse[:limit]:
        fit = get_stored_fit(job) or {}
        ready_jobs.append(
            ReadyJob(
                id=job.id,
                company=_company_name(job),
                role=job.title or "(untitled)",
                band=str(fit.get("band") or ""),
                posted_at=_posted_iso(job),
                platform=plat,
                job_url=(job.company_apply_url or job.canonical_url),
                prepare_path=f"/jobs/{job.id}",
            )
        )

    gh_count = counts.get(PLATFORM_GREENHOUSE, 0)
    ashby_n = counts.get(PLATFORM_ASHBY, 0)
    lever_n = counts.get(PLATFORM_LEVER, 0)
    recommendation = None
    if ashby_n > gh_count or lever_n > gh_count:
        parts = []
        if ashby_n > gh_count:
            parts.append(
                f"Ashby has {ashby_n} otherwise-ready jobs vs {gh_count} Greenhouse"
            )
        if lever_n > gh_count:
            parts.append(
                f"Lever has {lever_n} otherwise-ready jobs vs {gh_count} Greenhouse"
            )
        recommendation = (
            "; ".join(parts)
            + ". Consider adding autofill support for the larger platform(s)."
        )

    return ReadyToApplyPayload(
        jobs=ready_jobs,
        counts_by_platform=counts,
        greenhouse_ready=gh_count,
        recommendation=recommendation,
        total_otherwise_ready=len(otherwise),
    )
