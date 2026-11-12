from __future__ import annotations

from datetime import UTC, date, datetime, time
from math import ceil
from typing import Any

from sqlalchemy import ColumnElement, func, or_, text
from sqlalchemy.orm import Query, Session, joinedload

from apps.dashboard.schemas import (
    FitBandCounts,
    FitFeedFilter,
    JobDashboardItem,
    JobQueryParams,
    JobSort,
    JobsListResponse,
    RecencyCounts,
    RecencyFilter,
    SourceOption,
)
from packages.core.domain.models import Company, JobPosting
from packages.core.services.job_display import source_display_label
from packages.core.services.job_eligibility import (
    ELIGIBILITY_ACTIONABLE,
    ELIGIBILITY_EXCLUDED,
    resolve_job_eligibility,
)
from packages.core.services.evidence_fit_service import (
    BAND_LOW_FIT,
    BAND_NEEDS_REVIEW,
    BAND_RECOMMENDED,
    BAND_STRETCH,
    get_stored_fit,
)
from packages.core.services.job_recency import (
    RecencyWindow,
    first_seen_at,
    is_new_since_refresh,
    is_within_window,
    job_recency_payload,
    recency_counts,
    recency_timestamp,
)
from packages.core.services.job_refresh_service import (
    get_refresh_status,
    stale_threshold_hours,
)


def job_url_for(job: JobPosting) -> str | None:
    """Prefer company apply URL; fall back to canonical posting/detail URL."""
    for candidate in (job.company_apply_url, job.canonical_url):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def date_found_for(job: JobPosting) -> datetime | None:
    """Best honest 'when we found this job' timestamp (not necessarily posted date)."""
    return job.scraped_at or job.discovered_at or job.created_at


def company_name_for(job: JobPosting) -> str:
    if job.company and job.company.canonical_name:
        return job.company.canonical_name
    return "Unknown company"


def location_for(job: JobPosting) -> str:
    if job.location and job.location.strip():
        return job.location.strip()
    return "Not specified"


def source_value_for(job: JobPosting) -> str:
    return (job.source_name or job.source or "unknown").strip() or "unknown"


def to_dashboard_item(
    job: JobPosting,
    *,
    last_refresh_started_at: datetime | None = None,
) -> JobDashboardItem:
    source_value = source_value_for(job)
    fit = get_stored_fit(job) or {}
    eligibility = resolve_job_eligibility(job)
    recency = job_recency_payload(job)
    is_new = is_new_since_refresh(
        job, last_refresh_started_at=last_refresh_started_at
    )
    return JobDashboardItem(
        id=job.id,
        title=job.title,
        company=company_name_for(job),
        location=location_for(job),
        source=source_value,
        source_label=source_display_label(source_value),
        source_type=job.source_type,
        date_found=date_found_for(job),
        posted_at=job.posted_at,
        created_at=job.created_at,
        job_url=job_url_for(job),
        status=job.status,
        fit_band=fit.get("band"),
        fit_score=fit.get("score"),
        fit_reason=fit.get("short_reason"),
        fit_confidence=fit.get("confidence"),
        eligibility=eligibility.result,
        recency_at=recency.get("recency_at"),
        recency_label=recency.get("recency_label"),
        first_seen_at=first_seen_at(job),
        is_new_since_refresh=is_new,
    )


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=UTC)


def _day_end(d: date) -> datetime:
    return datetime.combine(d, time.max, tzinfo=UTC)


def _date_found_expr() -> ColumnElement[Any]:
    return func.coalesce(
        JobPosting.scraped_at, JobPosting.discovered_at, JobPosting.created_at
    )


def _apply_filters(query: Query[Any], params: JobQueryParams) -> Query[Any]:
    if params.q:
        pattern = f"%{params.q}%"
        query = query.filter(
            or_(
                JobPosting.title.ilike(pattern),
                Company.canonical_name.ilike(pattern),
            )
        )
    if params.source:
        query = query.filter(
            or_(
                JobPosting.source_name == params.source,
                JobPosting.source == params.source,
            )
        )
    if params.location:
        pattern = f"%{params.location}%"
        query = query.filter(JobPosting.location.ilike(pattern))
    if params.date_from is not None:
        query = query.filter(_date_found_expr() >= _day_start(params.date_from))
    if params.date_to is not None:
        query = query.filter(_date_found_expr() <= _day_end(params.date_to))
    return query


def _apply_sort(query: Query[Any], sort: JobSort) -> Query[Any]:
    date_found_expr = _date_found_expr()
    company_name = func.coalesce(Company.canonical_name, "")
    location_val = func.coalesce(JobPosting.location, "")
    source_val = func.coalesce(JobPosting.source_name, JobPosting.source, "")

    order_map: dict[JobSort, tuple[Any, Any]] = {
        JobSort.date_desc: (date_found_expr.desc(), JobPosting.id.asc()),
        JobSort.date_asc: (date_found_expr.asc(), JobPosting.id.asc()),
        JobSort.title_asc: (JobPosting.title.asc(), JobPosting.id.asc()),
        JobSort.title_desc: (JobPosting.title.desc(), JobPosting.id.asc()),
        JobSort.company_asc: (company_name.asc(), JobPosting.id.asc()),
        JobSort.company_desc: (company_name.desc(), JobPosting.id.asc()),
        JobSort.location_asc: (location_val.asc(), JobPosting.id.asc()),
        JobSort.location_desc: (location_val.desc(), JobPosting.id.asc()),
        JobSort.source_asc: (source_val.asc(), JobPosting.id.asc()),
        JobSort.source_desc: (source_val.desc(), JobPosting.id.asc()),
        JobSort.fit_desc: (date_found_expr.desc(), JobPosting.id.asc()),
    }
    primary, secondary = order_map[sort]
    return query.order_by(primary, secondary)


def list_available_sources(session: Session) -> list[SourceOption]:
    rows = (
        session.query(
            func.coalesce(JobPosting.source_name, JobPosting.source).label("src"),
            func.count(JobPosting.id),
        )
        .group_by(func.coalesce(JobPosting.source_name, JobPosting.source))
        .order_by(func.count(JobPosting.id).desc())
        .all()
    )
    options: list[SourceOption] = []
    for src, count in rows:
        value = (src or "unknown").strip() or "unknown"
        options.append(
            SourceOption(
                value=value,
                label=source_display_label(value),
                count=int(count),
            )
        )
    return options


def _matches_fit_filter(job: JobPosting, fit: FitFeedFilter) -> bool:
    eligibility = resolve_job_eligibility(job)
    if fit != FitFeedFilter.all and eligibility.result == ELIGIBILITY_EXCLUDED:
        return False
    band = (get_stored_fit(job) or {}).get("band")
    if fit == FitFeedFilter.all:
        return True
    if fit == FitFeedFilter.all_eligible:
        return eligibility.result == ELIGIBILITY_ACTIONABLE
    if fit == FitFeedFilter.recommended:
        return (
            eligibility.result == ELIGIBILITY_ACTIONABLE
            and band == BAND_RECOMMENDED
        )
    if fit == FitFeedFilter.recommended_stretch:
        return eligibility.result == ELIGIBILITY_ACTIONABLE and band in {
            BAND_RECOMMENDED,
            BAND_STRETCH,
        }
    if fit == FitFeedFilter.needs_review:
        return band == BAND_NEEDS_REVIEW or band is None
    if fit == FitFeedFilter.low_fit:
        return band == BAND_LOW_FIT
    return True


def compute_fit_counts(jobs: list[JobPosting]) -> FitBandCounts:
    counts = FitBandCounts()
    for job in jobs:
        eligibility = resolve_job_eligibility(job)
        if eligibility.result == ELIGIBILITY_ACTIONABLE:
            counts.all_eligible += 1
        if eligibility.result == ELIGIBILITY_EXCLUDED:
            continue
        band = (get_stored_fit(job) or {}).get("band")
        if band == BAND_RECOMMENDED:
            counts.recommended += 1
        elif band == BAND_STRETCH:
            counts.stretch += 1
        elif band == BAND_NEEDS_REVIEW:
            counts.needs_review += 1
        elif band == BAND_LOW_FIT:
            counts.low_fit += 1
        else:
            counts.unscored += 1
            counts.needs_review += 1
    return counts


def _recency_window(recency: RecencyFilter) -> RecencyWindow:
    return RecencyWindow(recency.value)


def _matches_recency_filter(
    job: JobPosting,
    recency: RecencyFilter,
    *,
    last_refresh_started_at: datetime | None,
) -> bool:
    return is_within_window(
        job,
        _recency_window(recency),
        last_refresh_started_at=last_refresh_started_at,
    )


def _to_recency_counts(raw: dict[str, int]) -> RecencyCounts:
    return RecencyCounts(
        hours_24=raw.get("24h", 0),
        hours_48=raw.get("48h", 0),
        days_7=raw.get("7d", 0),
        days_30=raw.get("30d", 0),
        all=raw.get("all", 0),
        unknown_or_approx=raw.get("unknown_or_approx", 0),
    )


def _recency_sort_key(job: JobPosting) -> datetime:
    ts, _label = recency_timestamp(job)
    if ts is None:
        return datetime.min.replace(tzinfo=UTC)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def list_dashboard_jobs(session: Session, params: JobQueryParams) -> JobsListResponse:
    """Return jobs matching filters. Fit bands are separate from eligibility."""
    refresh_meta = get_refresh_status(session)
    last_refresh_raw = refresh_meta.get("last_refresh_started_at")
    last_refresh_started_at: datetime | None = None
    if last_refresh_raw:
        try:
            last_refresh_started_at = datetime.fromisoformat(last_refresh_raw)
        except ValueError:
            last_refresh_started_at = None

    list_q: Query[Any] = (
        session.query(JobPosting)
        .outerjoin(Company, JobPosting.company_id == Company.id)
        .options(joinedload(JobPosting.company))
    )
    list_q = _apply_filters(list_q, params)
    list_q = _apply_sort(list_q, params.sort)
    all_rows = list_q.all()
    fit_counts = compute_fit_counts(all_rows)
    fit_filtered = [job for job in all_rows if _matches_fit_filter(job, params.fit)]
    raw_recency = recency_counts(
        fit_filtered, last_refresh_started_at=last_refresh_started_at
    )
    filtered = [
        job
        for job in fit_filtered
        if _matches_recency_filter(
            job, params.recency, last_refresh_started_at=last_refresh_started_at
        )
    ]
    # Newest-first within selected filters uses the honest recency clock
    # (posted_at, else first_seen), not scraped_at alone.
    if params.sort == JobSort.date_desc:
        filtered.sort(key=_recency_sort_key, reverse=True)
    elif params.sort == JobSort.date_asc:
        filtered.sort(key=_recency_sort_key)
    elif params.sort == JobSort.fit_desc:
        filtered.sort(
            key=lambda job: int((get_stored_fit(job) or {}).get("score") or -1),
            reverse=True,
        )

    total = len(filtered)
    total_pages = max(1, ceil(total / params.page_size)) if total else 0
    page = min(params.page, total_pages) if total_pages else 1
    offset = (page - 1) * params.page_size
    page_rows = filtered[offset : offset + params.page_size]

    last_refreshed = None
    overall = refresh_meta.get("overall_last_refreshed")
    if overall:
        try:
            last_refreshed = datetime.fromisoformat(overall)
        except ValueError:
            last_refreshed = None

    return JobsListResponse(
        items=[
            to_dashboard_item(job, last_refresh_started_at=last_refresh_started_at)
            for job in page_rows
        ],
        page=page,
        page_size=params.page_size,
        total=total,
        total_pages=int(total_pages),
        available_sources=list_available_sources(session),
        fit_counts=fit_counts,
        fit_filter=params.fit.value,
        recency_filter=params.recency.value,
        recency_counts=_to_recency_counts(raw_recency),
        last_refreshed=last_refreshed,
        is_stale=bool(refresh_meta.get("is_stale")),
        stale_threshold_hours=stale_threshold_hours(),
    )


def check_database(session: Session) -> None:
    session.execute(text("SELECT 1"))
