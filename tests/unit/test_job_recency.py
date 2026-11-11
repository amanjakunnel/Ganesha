from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from packages.core.services.job_recency import (
    RecencyWindow,
    is_within_window,
    recency_timestamp,
)


def _job(**kwargs):
    defaults = {
        "posted_at": None,
        "discovered_at": None,
        "created_at": None,
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
