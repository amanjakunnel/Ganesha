"""Job feed recency helpers.

Definition of "recent" for a job:
  1. Prefer the source posted date when present (SimplifyJobs age → posted_at).
  2. Otherwise fall back to first_seen_at (= discovered_at).

Approximate / unknown first-seen values are labeled and never counted as "new"
in recency windows (they must not inflate Last 24 hours).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from packages.core.domain.models import JobPosting

# Scraped-at vs discovered_at gap that suggests DB server_default now() skew
# relative to the Python ingestion clock (never invent a posted date from this).
SKEW_THRESHOLD = timedelta(hours=48)


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


def first_seen_is_approximate(job: JobPosting) -> bool:
    meta = job.intake_metadata or {}
    return bool(meta.get("first_seen_approx") or meta.get("first_seen_unknown"))


def ensure_first_seen_metadata(job: JobPosting) -> bool:
    """Mark unreliable first-seen stamps; never invent posted_at.

    Returns True if metadata was changed.

    When discovered_at is missing or clearly DB-clock-skewed vs scraped_at on
    first ingest, mark it approximate/unknown. Do **not** rewrite discovered_at
    forward to scraped_at — that would falsely count the row as newly seen.
    """
    meta = dict(job.intake_metadata or {})
    changed = False

    if job.discovered_at is None and job.created_at is not None:
        job.discovered_at = job.created_at
        meta["first_seen_approx"] = True
        meta["first_seen_unknown"] = False
        meta["first_seen_approx_note"] = (
            "discovered_at was missing; backfilled from created_at "
            "(not a source posted date). Treated as approximate for recency."
        )
        changed = True
    elif (
        job.scraped_at is not None
        and job.discovered_at is not None
        and abs(_as_utc(job.scraped_at) - _as_utc(job.discovered_at)) > SKEW_THRESHOLD
        and not (meta.get("last_import"))
        and not meta.get("first_seen_approx")
        and not meta.get("first_seen_unknown")
    ):
        # Likely Postgres server_default now() drifted vs the Python scrape clock.
        # Keep discovered_at as-is but mark unknown so recency won't treat it as new.
        meta["first_seen_unknown"] = True
        meta["first_seen_approx"] = True
        meta["first_seen_approx_note"] = (
            "discovered_at appeared skewed vs scraped_at (likely DB clock). "
            "First-seen treated as unknown for recency; not a source posted date."
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
      - "First seen" when falling back to a reliable discovered_at / created_at
      - "First seen (approx)" / "First seen (unknown)" when metadata says so
    """
    meta = job.intake_metadata or {}
    if job.posted_at is not None:
        if meta.get("simplify_age_days") is not None:
            return job.posted_at, "Posted (source age)"
        return job.posted_at, "Posted"
    seen = first_seen_at(job)
    if meta.get("first_seen_unknown"):
        return seen, "First seen (unknown)"
    if meta.get("first_seen_approx"):
        return seen, "First seen (approx)"
    return seen, "First seen"


def _uses_unreliable_first_seen(job: JobPosting) -> bool:
    """True when the active recency clock is approximate first-seen (not posted_at)."""
    if job.posted_at is not None:
        return False
    return first_seen_is_approximate(job)


def is_within_window(
    job: JobPosting,
    window: RecencyWindow,
    *,
    now: datetime | None = None,
    last_refresh_started_at: datetime | None = None,
) -> bool:
    if window == RecencyWindow.all:
        return True

    # Approximate/unknown first-seen must not inflate "new" buckets.
    if _uses_unreliable_first_seen(job):
        if window in {
            RecencyWindow.hours_24,
            RecencyWindow.days_3,
            RecencyWindow.days_7,
            RecencyWindow.new_since_refresh,
        }:
            return False

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
    meta = job.intake_metadata or {}
    return {
        "recency_at": ts,
        "recency_label": label,
        "first_seen_at": first_seen_at(job),
        "first_seen_approx": bool(meta.get("first_seen_approx")),
        "first_seen_unknown": bool(meta.get("first_seen_unknown")),
        "posted_at": job.posted_at,
    }


def measure_first_seen_skew(jobs: list[JobPosting]) -> dict[str, Any]:
    """Aggregate report of discovered_at vs scraped_at skew (for ops / migration)."""
    total = len(jobs)
    skewed = 0
    deltas: list[float] = []
    already_flagged = 0
    for job in jobs:
        meta = job.intake_metadata or {}
        if meta.get("first_seen_approx") or meta.get("first_seen_unknown"):
            already_flagged += 1
        if job.scraped_at is None or job.discovered_at is None:
            continue
        delta = (_as_utc(job.scraped_at) - _as_utc(job.discovered_at)).total_seconds()
        if abs(delta) > SKEW_THRESHOLD.total_seconds() and not meta.get("last_import"):
            skewed += 1
            deltas.append(delta)
        elif abs(delta) > SKEW_THRESHOLD.total_seconds() and meta.get("last_import"):
            # Re-scraped older rows: discovered_at is historical first-seen, not skew.
            pass
    return {
        "total_jobs": total,
        "skew_no_last_import_gt_48h": skewed,
        "already_flagged_approx": already_flagged,
        "delta_sec_min": min(deltas) if deltas else None,
        "delta_sec_max": max(deltas) if deltas else None,
    }
