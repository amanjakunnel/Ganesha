"""Job feed recency helpers.

Definition of "recent" for a job:
  1. Prefer the source posted date when present (SimplifyJobs age → posted_at).
  2. Otherwise fall back to first_seen_at (= discovered_at).

Labels exposed to the UI so the user is never misled about which clock is used.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from packages.core.domain.models import JobPosting


class RecencyWindow(str, Enum):
    hours_24 = "24h"
    days_3 = "3d"
    days_7 = "7d"
    all = "all"
    new_since_refresh = "new_since_refresh"


WINDOW_DELTAS: dict[RecencyWindow, timedelta | None] = {
    RecencyWindow.hours_24: timedelta(hours=24),
    RecencyWindow.days_3: timedelta(days=3),
    RecencyWindow.days_7: timedelta(days=7),
    RecencyWindow.all: None,
    RecencyWindow.new_since_refresh: None,
}


def first_seen_at(job: JobPosting) -> datetime | None:
    """Ganesha first-seen timestamp. Prefer discovered_at; never invent posted_at."""
    return job.discovered_at or job.created_at


def ensure_first_seen_metadata(job: JobPosting) -> bool:
    """Backfill discovered_at when missing or clearly DB-clock-skewed; mark approximation.

    Returns True if metadata was changed. Never invents posted_at.
    """
    meta = dict(job.intake_metadata or {})
    changed = False
    if job.discovered_at is None and job.created_at is not None:
        job.discovered_at = job.created_at
        meta["first_seen_approx"] = True
        meta["first_seen_approx_note"] = (
            "discovered_at was missing; backfilled from created_at "
            "(not a source posted date)."
        )
        changed = True
    # Repair rows where Postgres server_default now() lagged Python scraped_at
    # by > 48h on first ingest (no prior last_import → newly created this sync era).
    elif (
        job.scraped_at is not None
        and job.discovered_at is not None
        and (_as_utc(job.scraped_at) - _as_utc(job.discovered_at)).total_seconds()
        > 48 * 3600
        and not (meta.get("last_import"))
        and not meta.get("first_seen_approx")
    ):
        job.discovered_at = job.scraped_at
        meta["first_seen_approx"] = True
        meta["first_seen_approx_note"] = (
            "discovered_at appeared skewed vs scraped_at (likely DB clock); "
            "approximated from scraped_at. Not a source posted date."
        )
        changed = True
    elif "first_seen_at_note" not in meta:
        meta["first_seen_at_note"] = (
            "first_seen_at is discovered_at (ingestion time), not employer posted date."
        )
        changed = True
    if changed:
        job.intake_metadata = meta
    return changed


def recency_timestamp(job: JobPosting) -> tuple[datetime | None, str]:
    """Return (timestamp, label) used for recency filtering and display.

    Labels:
      - "Posted" when posted_at is present from a non-age source marker
      - "Posted (source age)" when SimplifyJobs age_days drove posted_at
      - "First seen" when falling back to discovered_at / created_at
    """
    meta = job.intake_metadata or {}
    if job.posted_at is not None:
        if meta.get("simplify_age_days") is not None:
            return job.posted_at, "Posted (source age)"
        return job.posted_at, "Posted"
    seen = first_seen_at(job)
    return seen, "First seen"


def is_within_window(
    job: JobPosting,
    window: RecencyWindow,
    *,
    now: datetime | None = None,
    last_refresh_started_at: datetime | None = None,
) -> bool:
    if window == RecencyWindow.all:
        return True
    if window == RecencyWindow.new_since_refresh:
        if last_refresh_started_at is None:
            return False
        seen = first_seen_at(job)
        if seen is None:
            return False
        # Inclusive: jobs first seen at/after the refresh start count as new.
        return _as_utc(seen) >= _as_utc(last_refresh_started_at)

    delta = WINDOW_DELTAS.get(window)
    if delta is None:
        return True
    ts, _label = recency_timestamp(job)
    if ts is None:
        return False
    cutoff = _as_utc(now or datetime.now(UTC)) - delta
    # Inclusive boundary: exactly 24h ago still counts as within last 24 hours.
    return _as_utc(ts) >= cutoff


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def recency_counts(
    jobs: list[JobPosting],
    *,
    now: datetime | None = None,
    last_refresh_started_at: datetime | None = None,
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    counts = {w.value: 0 for w in RecencyWindow}
    for job in jobs:
        for window in RecencyWindow:
            if is_within_window(
                job,
                window,
                now=now,
                last_refresh_started_at=last_refresh_started_at,
            ):
                counts[window.value] += 1
    return counts


def job_recency_payload(job: JobPosting) -> dict[str, Any]:
    ts, label = recency_timestamp(job)
    return {
        "recency_at": ts,
        "recency_label": label,
        "first_seen_at": first_seen_at(job),
        "posted_at": job.posted_at,
    }
