from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from apps.dashboard.schemas import JobQueryParams, JobSort
from apps.dashboard.services.jobs_dashboard_service import (
    job_url_for,
    list_dashboard_jobs,
    to_dashboard_item,
)
from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import (
    Base,
    Company,
    JobPosting,
    make_dedupe_key,
    make_description_hash,
    make_uuid,
)


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.query(JobPosting).delete()
        db.query(Company).delete()
        db.commit()
        yield db
    finally:
        db.rollback()
        db.close()


def _company(session: Session, name: str) -> Company:
    existing = (
        session.query(Company).filter(Company.canonical_name == name).one_or_none()
    )
    if existing:
        return existing
    c = Company(canonical_name=name, normalized_key=name.lower())
    session.add(c)
    session.flush()
    return c


def _add_job(
    session: Session,
    *,
    title: str,
    company_name: str | None = "Acme",
    location: str | None = "Remote",
    source_name: str = "linkedin",
    source: str = "csv_import",
    status: str = "new",
    apply_url: str | None = "https://example.com/apply/1",
    canonical_url: str | None = None,
    scraped_at: datetime | None = None,
    discovered_at: datetime | None = None,
) -> JobPosting:
    company = _company(session, company_name) if company_name else None
    now = datetime.now(UTC)
    job = JobPosting(
        id=make_uuid(),
        source=source,
        source_name=source_name,
        source_type="csv_import",
        title=title,
        company_id=company.id if company else None,
        location=location,
        description_text=f"Description for {title}",
        description_hash=make_description_hash(f"Description for {title}"),
        dedupe_key=make_dedupe_key(company_name or "none", title, location),
        company_apply_url=apply_url,
        canonical_url=canonical_url,
        status=status,
        scraped_at=scraped_at or now,
        discovered_at=discovered_at or now,
        source_import_key=f"dash-test|{make_uuid()}",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_lists_all_statuses_including_skipped(session: Session) -> None:
    _add_job(session, title="Open Role", status="new")
    _add_job(session, title="Skipped Role", status="skipped")
    _add_job(session, title="Archived Role", status="archived")
    result = list_dashboard_jobs(session, JobQueryParams())
    titles = {item.title for item in result.items}
    assert titles == {"Open Role", "Skipped Role", "Archived Role"}
    assert result.total == 3


def test_keyword_matches_title_case_insensitive(session: Session) -> None:
    _add_job(session, title="Senior Engineer")
    _add_job(session, title="Product Manager")
    result = list_dashboard_jobs(session, JobQueryParams(q="engineer"))
    assert [i.title for i in result.items] == ["Senior Engineer"]


def test_keyword_matches_company_case_insensitive(session: Session) -> None:
    _add_job(session, title="Role A", company_name="TikTok")
    _add_job(session, title="Role B", company_name="OtherCo")
    result = list_dashboard_jobs(session, JobQueryParams(q="tiktok"))
    assert len(result.items) == 1
    assert result.items[0].company == "TikTok"


def test_source_filter(session: Session) -> None:
    _add_job(session, title="LI", source_name="linkedin")
    _add_job(session, title="SJ", source_name="simplifyjobs")
    result = list_dashboard_jobs(session, JobQueryParams(source="simplifyjobs"))
    assert [i.title for i in result.items] == ["SJ"]
    assert result.items[0].source == "simplifyjobs"
    assert result.items[0].source_label == "simplifyjobs"


def test_location_partial_case_insensitive(session: Session) -> None:
    _add_job(session, title="NY Role", location="New York, NY")
    _add_job(session, title="SF Role", location="San Francisco, CA")
    result = list_dashboard_jobs(session, JobQueryParams(location="york"))
    assert [i.title for i in result.items] == ["NY Role"]


def test_date_from_to_filter(session: Session) -> None:
    old = datetime(2026, 1, 1, tzinfo=UTC)
    mid = datetime(2026, 2, 15, tzinfo=UTC)
    new = datetime(2026, 3, 20, tzinfo=UTC)
    _add_job(session, title="Old", scraped_at=old)
    _add_job(session, title="Mid", scraped_at=mid)
    _add_job(session, title="New", scraped_at=new)
    result = list_dashboard_jobs(
        session,
        JobQueryParams(date_from=mid.date(), date_to=mid.date()),
    )
    assert [i.title for i in result.items] == ["Mid"]


def test_sort_title_and_company(session: Session) -> None:
    _add_job(session, title="Bravo", company_name="Zebra")
    _add_job(session, title="Alpha", company_name="Apple")
    by_title = list_dashboard_jobs(session, JobQueryParams(sort=JobSort.title_asc))
    assert [i.title for i in by_title.items] == ["Alpha", "Bravo"]
    by_company = list_dashboard_jobs(session, JobQueryParams(sort=JobSort.company_asc))
    assert [i.company for i in by_company.items] == ["Apple", "Zebra"]


def test_sort_date_desc_default(session: Session) -> None:
    older = datetime.now(UTC) - timedelta(days=5)
    newer = datetime.now(UTC) - timedelta(days=1)
    _add_job(session, title="Older", scraped_at=older)
    _add_job(session, title="Newer", scraped_at=newer)
    result = list_dashboard_jobs(session, JobQueryParams())
    assert [i.title for i in result.items] == ["Newer", "Older"]


def test_pagination_stable(session: Session) -> None:
    for i in range(5):
        _add_job(
            session,
            title=f"Job {i:02d}",
            scraped_at=datetime(2026, 1, i + 1, tzinfo=UTC),
        )
    page1 = list_dashboard_jobs(
        session, JobQueryParams(page=1, page_size=2, sort=JobSort.date_asc)
    )
    page2 = list_dashboard_jobs(
        session, JobQueryParams(page=2, page_size=2, sort=JobSort.date_asc)
    )
    assert page1.total == 5
    assert page1.total_pages == 3
    assert len(page1.items) == 2
    assert len(page2.items) == 2
    assert {i.id for i in page1.items}.isdisjoint({i.id for i in page2.items})


def test_null_company_location_url(session: Session) -> None:
    job = _add_job(
        session,
        title="Sparse",
        company_name=None,
        location=None,
        apply_url=None,
        canonical_url=None,
    )
    item = to_dashboard_item(job)
    assert item.company == "Unknown company"
    assert item.location == "Not specified"
    assert item.job_url is None


def test_job_url_prefers_apply_over_canonical(session: Session) -> None:
    job = _add_job(
        session,
        title="URL Job",
        apply_url="https://company.example/apply",
        canonical_url="https://canonical.example/job",
    )
    assert job_url_for(job) == "https://company.example/apply"
    job2 = _add_job(
        session,
        title="Canonical Only",
        apply_url=None,
        canonical_url="https://canonical.example/only",
    )
    assert job_url_for(job2) == "https://canonical.example/only"


def test_available_sources_counts(session: Session) -> None:
    _add_job(session, title="A", source_name="linkedin")
    _add_job(session, title="B", source_name="linkedin")
    _add_job(session, title="C", source_name="simplifyjobs")
    result = list_dashboard_jobs(session, JobQueryParams())
    by_value = {s.value: s for s in result.available_sources}
    assert by_value["linkedin"].count == 2
    assert by_value["linkedin"].label == "linkedin_import"
    assert by_value["simplifyjobs"].count == 1
