from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from apps.dashboard.schemas import FitFeedFilter, JobQueryParams, JobSort, RecencyFilter
from apps.dashboard.services.jobs_dashboard_service import (
    job_url_for,
    list_dashboard_jobs,
    to_dashboard_item,
)
from packages.core.services.evidence_fit_service import (
    BAND_LOW_FIT,
    BAND_RECOMMENDED,
    BAND_STRETCH,
)
from packages.core.services.job_eligibility import ELIGIBILITY_EXCLUDED
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
    posted_at: datetime | None = None,
    description_text: str | None = None,
    intake_metadata: dict | None = None,
) -> JobPosting:
    company = _company(session, company_name) if company_name else None
    now = datetime.now(UTC)
    desc = description_text or (
        f"Description for {title}. Build reliable software systems with "
        "Python, distributed services, and thoughtful product delivery."
    )
    job = JobPosting(
        id=make_uuid(),
        source=source,
        source_name=source_name,
        source_type="csv_import",
        title=title,
        company_id=company.id if company else None,
        location=location,
        description_text=desc,
        description_hash=make_description_hash(desc),
        dedupe_key=make_dedupe_key(company_name or "none", title, location),
        company_apply_url=apply_url,
        canonical_url=canonical_url,
        status=status,
        scraped_at=scraped_at or now,
        discovered_at=discovered_at or now,
        posted_at=posted_at,
        intake_metadata=intake_metadata or {},
        source_import_key=f"dash-test|{make_uuid()}",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _fit_meta(band: str, score: int = 80) -> dict:
    return {
        "evidence_fit": {
            "band": band,
            "score": score,
            "short_reason": f"{band} stub",
            "confidence": "high",
        }
    }


def test_lists_all_statuses_including_skipped(session: Session) -> None:
    _add_job(session, title="Open Role", status="new")
    _add_job(session, title="Skipped Role", status="skipped")
    _add_job(session, title="Archived Role", status="archived")
    result = list_dashboard_jobs(session, JobQueryParams(fit=FitFeedFilter.all))
    titles = {item.title for item in result.items}
    assert titles == {"Open Role", "Skipped Role", "Archived Role"}
    assert result.total == 3


def test_keyword_matches_title_case_insensitive(session: Session) -> None:
    _add_job(session, title="Senior Engineer")
    _add_job(session, title="Product Manager")
    result = list_dashboard_jobs(
        session, JobQueryParams(fit=FitFeedFilter.all, q="engineer")
    )
    assert [i.title for i in result.items] == ["Senior Engineer"]


def test_keyword_matches_company_case_insensitive(session: Session) -> None:
    _add_job(session, title="Role A", company_name="TikTok")
    _add_job(session, title="Role B", company_name="OtherCo")
    result = list_dashboard_jobs(
        session, JobQueryParams(fit=FitFeedFilter.all, q="tiktok")
    )
    assert len(result.items) == 1
    assert result.items[0].company == "TikTok"


def test_source_filter(session: Session) -> None:
    _add_job(session, title="LI", source_name="linkedin")
    _add_job(session, title="SJ", source_name="simplifyjobs")
    result = list_dashboard_jobs(
        session, JobQueryParams(fit=FitFeedFilter.all, source="simplifyjobs")
    )
    assert [i.title for i in result.items] == ["SJ"]
    assert result.items[0].source == "simplifyjobs"
    assert result.items[0].source_label == "simplifyjobs"


def test_location_partial_case_insensitive(session: Session) -> None:
    _add_job(session, title="NY Role", location="New York, NY")
    _add_job(session, title="SF Role", location="San Francisco, CA")
    result = list_dashboard_jobs(
        session, JobQueryParams(fit=FitFeedFilter.all, location="york")
    )
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
        JobQueryParams(
            fit=FitFeedFilter.all, date_from=mid.date(), date_to=mid.date()
        ),
    )
    assert [i.title for i in result.items] == ["Mid"]


def test_sort_title_and_company(session: Session) -> None:
    _add_job(session, title="Bravo", company_name="Zebra")
    _add_job(session, title="Alpha", company_name="Apple")
    by_title = list_dashboard_jobs(
        session, JobQueryParams(fit=FitFeedFilter.all, sort=JobSort.title_asc)
    )
    assert [i.title for i in by_title.items] == ["Alpha", "Bravo"]
    by_company = list_dashboard_jobs(
        session, JobQueryParams(fit=FitFeedFilter.all, sort=JobSort.company_asc)
    )
    assert [i.company for i in by_company.items] == ["Apple", "Zebra"]


def test_sort_date_desc_default(session: Session) -> None:
    older = datetime.now(UTC) - timedelta(days=5)
    newer = datetime.now(UTC) - timedelta(days=1)
    _add_job(session, title="Older", scraped_at=older, posted_at=older)
    _add_job(session, title="Newer", scraped_at=newer, posted_at=newer)
    result = list_dashboard_jobs(session, JobQueryParams(fit=FitFeedFilter.all))
    assert [i.title for i in result.items] == ["Newer", "Older"]


def test_pagination_stable(session: Session) -> None:
    for i in range(5):
        _add_job(
            session,
            title=f"Job {i:02d}",
            scraped_at=datetime(2026, 1, i + 1, tzinfo=UTC),
            posted_at=datetime(2026, 1, i + 1, tzinfo=UTC),
        )
    page1 = list_dashboard_jobs(
        session,
        JobQueryParams(
            fit=FitFeedFilter.all, page=1, page_size=2, sort=JobSort.date_asc
        ),
    )
    page2 = list_dashboard_jobs(
        session,
        JobQueryParams(
            fit=FitFeedFilter.all, page=2, page_size=2, sort=JobSort.date_asc
        ),
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
    result = list_dashboard_jobs(session, JobQueryParams(fit=FitFeedFilter.all))
    by_value = {s.value: s for s in result.available_sources}
    assert by_value["linkedin"].count == 2
    assert by_value["linkedin"].label == "linkedin_import"
    assert by_value["simplifyjobs"].count == 1


def test_recency_presets_and_unknown_counts(session: Session) -> None:
    now = datetime.now(UTC)
    _add_job(
        session,
        title="Fresh Posted",
        posted_at=now - timedelta(hours=12),
        intake_metadata=_fit_meta(BAND_RECOMMENDED),
    )
    _add_job(
        session,
        title="Two Day Old",
        posted_at=now - timedelta(hours=36),
        intake_metadata=_fit_meta(BAND_RECOMMENDED),
    )
    _add_job(
        session,
        title="Ten Day Old",
        posted_at=now - timedelta(days=10),
        intake_metadata=_fit_meta(BAND_RECOMMENDED),
    )
    _add_job(
        session,
        title="Unknown First Seen",
        discovered_at=now - timedelta(hours=1),
        intake_metadata={
            **_fit_meta(BAND_RECOMMENDED),
            "first_seen_unknown": True,
            "first_seen_approx": True,
        },
    )
    all_fit = list_dashboard_jobs(
        session,
        JobQueryParams(fit=FitFeedFilter.recommended, recency=RecencyFilter.all),
    )
    assert all_fit.recency_counts.unknown_or_approx == 1
    assert all_fit.recency_counts.hours_24 == 1
    assert all_fit.recency_counts.hours_48 == 2
    assert all_fit.recency_counts.days_7 == 2
    assert all_fit.recency_counts.days_30 == 3
    assert all_fit.recency_counts.all == 4

    only_24h = list_dashboard_jobs(
        session,
        JobQueryParams(fit=FitFeedFilter.recommended, recency=RecencyFilter.hours_24),
    )
    assert [i.title for i in only_24h.items] == ["Fresh Posted"]
    assert only_24h.items[0].recency_label == "Posted"

    only_48h = list_dashboard_jobs(
        session,
        JobQueryParams(fit=FitFeedFilter.recommended, recency=RecencyFilter.hours_48),
    )
    assert {i.title for i in only_48h.items} == {"Fresh Posted", "Two Day Old"}


def test_recency_combines_with_fit_and_excludes_ineligible(
    session: Session,
) -> None:
    now = datetime.now(UTC)
    _add_job(
        session,
        title="Recommended Fresh",
        posted_at=now - timedelta(hours=6),
        intake_metadata=_fit_meta(BAND_RECOMMENDED, score=90),
    )
    _add_job(
        session,
        title="Stretch Fresh",
        posted_at=now - timedelta(hours=6),
        intake_metadata=_fit_meta(BAND_STRETCH, score=55),
    )
    _add_job(
        session,
        title="Low Fit Fresh",
        posted_at=now - timedelta(hours=6),
        intake_metadata=_fit_meta(BAND_LOW_FIT, score=20),
    )
    _add_job(
        session,
        title="PhD New Grad Internship",
        posted_at=now - timedelta(hours=6),
        description_text=(
            "This is an internship role for PhD required research scientists. "
            "Candidates must hold a PhD in computer science or related field."
        ),
        intake_metadata=_fit_meta(BAND_RECOMMENDED, score=99),
    )

    recommended = list_dashboard_jobs(
        session,
        JobQueryParams(
            fit=FitFeedFilter.recommended,
            recency=RecencyFilter.hours_24,
        ),
    )
    assert [i.title for i in recommended.items] == ["Recommended Fresh"]
    assert recommended.total == 1

    # Excluded eligibility never appears outside fit=all.
    stretch_view = list_dashboard_jobs(
        session,
        JobQueryParams(
            fit=FitFeedFilter.recommended_stretch,
            recency=RecencyFilter.hours_24,
        ),
    )
    titles = {i.title for i in stretch_view.items}
    assert titles == {"Recommended Fresh", "Stretch Fresh"}
    assert "PhD New Grad Internship" not in titles
    assert "Low Fit Fresh" not in titles

    all_view = list_dashboard_jobs(
        session,
        JobQueryParams(fit=FitFeedFilter.all, recency=RecencyFilter.hours_24),
    )
    phd = next(i for i in all_view.items if i.title == "PhD New Grad Internship")
    assert phd.eligibility == ELIGIBILITY_EXCLUDED

