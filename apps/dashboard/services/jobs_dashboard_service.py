from __future__ import annotations

from datetime import UTC, date, datetime, time
from math import ceil
from typing import Any

from sqlalchemy import ColumnElement, func, or_, text
from sqlalchemy.orm import Query, Session, joinedload

from apps.dashboard.schemas import (
    JobDashboardItem,
    JobQueryParams,
    JobSort,
    JobsListResponse,
    SourceOption,
)
from packages.core.domain.models import Company, JobPosting
from packages.core.services.job_display import source_display_label


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


def to_dashboard_item(job: JobPosting) -> JobDashboardItem:
    source_value = source_value_for(job)
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


def list_dashboard_jobs(session: Session, params: JobQueryParams) -> JobsListResponse:
    """Return every stored job matching filters (no actionable-queue exclusions)."""
    count_q: Query[Any] = session.query(func.count(JobPosting.id)).outerjoin(
        Company, JobPosting.company_id == Company.id
    )
    count_q = _apply_filters(count_q, params)
    total = int(count_q.scalar() or 0)
    total_pages = max(1, ceil(total / params.page_size)) if total else 0
    page = min(params.page, total_pages) if total_pages else 1

    list_q: Query[Any] = (
        session.query(JobPosting)
        .outerjoin(Company, JobPosting.company_id == Company.id)
        .options(joinedload(JobPosting.company))
    )
    list_q = _apply_filters(list_q, params)
    list_q = _apply_sort(list_q, params.sort)
    offset = (page - 1) * params.page_size
    rows = list_q.offset(offset).limit(params.page_size).all()

    return JobsListResponse(
        items=[to_dashboard_item(job) for job in rows],
        page=page,
        page_size=params.page_size,
        total=total,
        total_pages=int(total_pages),
        available_sources=list_available_sources(session),
    )


def check_database(session: Session) -> None:
    session.execute(text("SELECT 1"))
