from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from packages.core.services.job_recency import (
    RecencyWindow,
    ensure_first_seen_metadata,
    is_within_window,
    measure_first_seen_skew,
    recency_timestamp,
)


def _job(**kwargs):
    defaults = {
        "posted_at": None,
        "discovered_at": None,
        "created_at": None,
        "scraped_at": None,
        "intake_metadata": {},
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_prefers_posted_at_with_source_age_label() -> None:
    posted = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    job = _job(
        posted_at=posted,
        discovered_at=datetime(2026, 11, 1, tzinfo=UTC),
        intake_metadata={"simplify_age_days": 1},
    )
    ts, label = recency_timestamp(job)
    assert ts == posted
    assert label == "Posted (source age)"


def test_falls_back_to_first_seen() -> None:
    seen = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)
    job = _job(discovered_at=seen, intake_metadata={})
    ts, label = recency_timestamp(job)
    assert ts == seen
    assert label == "First seen"


def test_24h_boundary_inclusive() -> None:
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    exact = now - timedelta(hours=24)
    inside = now - timedelta(hours=23, minutes=59)
    outside = now - timedelta(hours=24, seconds=1)
    assert is_within_window(
        _job(posted_at=exact), RecencyWindow.hours_24, now=now
    )
    assert is_within_window(
        _job(posted_at=inside), RecencyWindow.hours_24, now=now
    )
    assert not is_within_window(
        _job(posted_at=outside), RecencyWindow.hours_24, now=now
    )


def test_new_since_refresh_uses_first_seen() -> None:
    started = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)
    new_job = _job(discovered_at=started)
    old_job = _job(discovered_at=started - timedelta(minutes=1))
    assert is_within_window(
        new_job,
        RecencyWindow.new_since_refresh,
        last_refresh_started_at=started,
    )
    assert not is_within_window(
        old_job,
        RecencyWindow.new_since_refresh,
        last_refresh_started_at=started,
    )


def test_skewed_first_seen_marked_unknown_not_rewritten() -> None:
    scraped = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    discovered = scraped - timedelta(days=5)
    job = _job(
        scraped_at=scraped,
        discovered_at=discovered,
        intake_metadata={},  # no last_import → first-ingest skew candidate
    )
    assert ensure_first_seen_metadata(job) is True
    assert job.discovered_at == discovered  # never invent a newer first-seen
    assert job.intake_metadata["first_seen_unknown"] is True
    assert job.intake_metadata["first_seen_approx"] is True
    _ts, label = recency_timestamp(job)
    assert label == "First seen (unknown)"
    assert not is_within_window(job, RecencyWindow.hours_24, now=scraped)
    assert not is_within_window(
        job,
        RecencyWindow.new_since_refresh,
        last_refresh_started_at=scraped - timedelta(hours=1),
    )


def test_approx_first_seen_excluded_from_24h_even_if_timestamp_recent() -> None:
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    job = _job(
        discovered_at=now - timedelta(hours=1),
        intake_metadata={"first_seen_approx": True},
    )
    assert not is_within_window(job, RecencyWindow.hours_24, now=now)
    # Reliable posted_at still counts.
    job_posted = _job(
        posted_at=now - timedelta(hours=1),
        discovered_at=now - timedelta(hours=1),
        intake_metadata={"first_seen_approx": True},
    )
    assert is_within_window(job_posted, RecencyWindow.hours_24, now=now)


def test_measure_skew_report() -> None:
    scraped = datetime(2026, 11, 11, tzinfo=UTC)
    jobs = [
        _job(
            scraped_at=scraped,
            discovered_at=scraped - timedelta(days=10),
            intake_metadata={},
        ),
        _job(
            scraped_at=scraped,
            discovered_at=scraped - timedelta(days=10),
            intake_metadata={"last_import": "x"},
        ),
    ]
    report = measure_first_seen_skew(jobs)
    assert report["total_jobs"] == 2
    assert report["skew_no_last_import_gt_48h"] == 1


def test_python_intake_stamps_discovered_at() -> None:
    """Regression: new jobs must get discovered_at from Python, not DB now() alone."""
    from packages.core.db import SessionLocal, get_engine
    from packages.core.domain.models import Base, JobPosting
    from packages.core.services.job_intake import upsert_job_from_import

    from packages.core.domain.models import make_uuid

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        before = datetime.now(UTC)
        suffix = make_uuid()
        job_id, created = upsert_job_from_import(
            session,
            {
                "company": "StampCo",
                "title": f"Stamp Engineer {suffix[:8]}",
                "description_text": f"Build things uniquely {suffix}.",
                "canonical_url": f"https://jobs.example-stamp.test/roles/{suffix}",
                "company_apply_url": f"https://jobs.example-stamp.test/roles/{suffix}",
                "source_import_key": f"stamp-test-{suffix}",
                "intake_metadata": {},
            },
            source_name="test",
            source_type="unit",
        )
        session.commit()
        after = datetime.now(UTC)
        assert created is True
        job = session.query(JobPosting).filter_by(id=job_id).one()
        assert job.discovered_at is not None
        da = (
            job.discovered_at
            if job.discovered_at.tzinfo
            else job.discovered_at.replace(tzinfo=UTC)
        )
        assert before - timedelta(seconds=2) <= da <= after + timedelta(seconds=2)
        assert job.scraped_at is not None
    finally:
        session.close()
