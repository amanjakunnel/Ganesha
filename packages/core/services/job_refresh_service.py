"""On-demand job feed refresh using existing source sync services.

Root cause of stale feeds: there is no in-app scheduler. SimplifyJobs sync
historically ran only via CLI (`jobs sync-simplifyjobs`). This service wraps
those same sync functions for the dashboard Refresh control.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from packages.core.db import SessionLocal
from packages.core.domain.models import AuditEvent, JobPosting
from packages.core.services.job_eligibility import ELIGIBILITY_EXCLUDED
from packages.core.services.job_recency import ensure_first_seen_metadata
from packages.core.services.board_discovery import sync_company_boards
from packages.core.services.simplifyjobs_sync import (
    SimplifyJobsFetchError,
    sync_simplifyjobs,
)
from packages.core.services.source_registry import (
    SOURCE_KEYS,
    _last_sync_at,
    record_source_sync,
)
from packages.core.settings import settings

DEFAULT_MIN_INTERVAL_SECONDS = 120
DEFAULT_TIMEOUT_SECONDS = 600
STALE_THRESHOLD_HOURS_DEFAULT = 24


@dataclass
class SourceRefreshResult:
    source_id: str
    label: str
    status: str  # success | failed | skipped | running
    fetched: int = 0
    new: int = 0
    updated: int = 0
    closed: int = 0
    companies_checked: int = 0
    errors: list[str] = field(default_factory=list)
    error_summary: str | None = None
    last_sync: str | None = None
    duration_ms: int | None = None


_LIVE_SOURCE_KINDS = frozenset({"github_sync", "board_api"})


@dataclass
class RefreshJobState:
    refresh_id: str
    status: str  # idle | running | completed | failed | rejected
    started_at: str | None = None
    finished_at: str | None = None
    progress: str = ""
    sources: list[SourceRefreshResult] = field(default_factory=list)
    fit_assessed: int = 0
    message: str | None = None
    overall_last_refreshed: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "refresh_id": self.refresh_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": self.progress,
            "sources": [asdict(s) for s in self.sources],
            "fit_assessed": self.fit_assessed,
            "message": self.message,
            "overall_last_refreshed": self.overall_last_refreshed,
        }


# Concurrent refresh guard + rate limit (process-local; dashboard is single-process).
_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_ACTIVE: RefreshJobState | None = None
_HISTORY: list[RefreshJobState] = []
_LAST_COMPLETED_AT: datetime | None = None


def stale_threshold_hours() -> int:
    return int(getattr(settings, "job_feed_stale_hours", STALE_THRESHOLD_HOURS_DEFAULT))


def min_refresh_interval_seconds() -> int:
    return int(
        getattr(settings, "job_refresh_min_interval_seconds", DEFAULT_MIN_INTERVAL_SECONDS)
    )


def refresh_timeout_seconds() -> int:
    return int(
        getattr(settings, "job_refresh_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    )


def get_refresh_status(session: Session | None = None) -> dict[str, Any]:
    with _STATE_LOCK:
        active = _ACTIVE
        last = _LAST_COMPLETED_AT
        history_tail = _HISTORY[-1] if _HISTORY else None

    own_session = session is None
    if own_session:
        session = SessionLocal()
    assert session is not None
    try:
        sources = _source_status_snapshot(session)
        overall = _overall_last_refreshed(session, last)
        age_hours = None
        is_stale = False
        if overall:
            overall_dt = datetime.fromisoformat(overall)
            if overall_dt.tzinfo is None:
                overall_dt = overall_dt.replace(tzinfo=UTC)
            else:
                overall_dt = overall_dt.astimezone(UTC)
            age_hours = (datetime.now(UTC) - overall_dt).total_seconds() / 3600.0
            is_stale = age_hours > stale_threshold_hours()
        payload: dict[str, Any] = {
            "active": active.to_dict() if active else None,
            "last_completed": history_tail.to_dict() if history_tail else None,
            "overall_last_refreshed": overall,
            "stale_threshold_hours": stale_threshold_hours(),
            "age_hours": round(age_hours, 2) if age_hours is not None else None,
            "is_stale": is_stale,
            "sources": sources,
            "min_interval_seconds": min_refresh_interval_seconds(),
            "last_refresh_started_at": (
                active.started_at
                if active and active.status == "running"
                else (history_tail.started_at if history_tail else None)
            ),
        }
        return payload
    finally:
        if own_session:
            session.close()


def start_refresh(*, force: bool = False) -> tuple[RefreshJobState, int]:
    """Start a background refresh. Returns (state, http_status_hint).

    202 = started, 409 = already running, 429 = rate limited.
    """
    global _ACTIVE, _LAST_COMPLETED_AT

    with _LOCK:
        with _STATE_LOCK:
            if _ACTIVE is not None and _ACTIVE.status == "running":
                return _ACTIVE, 409
            if not force and _LAST_COMPLETED_AT is not None:
                elapsed = (datetime.now(UTC) - _LAST_COMPLETED_AT).total_seconds()
                if elapsed < min_refresh_interval_seconds():
                    rejected = RefreshJobState(
                        refresh_id="rejected",
                        status="rejected",
                        message=(
                            f"Refresh rate-limited: wait "
                            f"{int(min_refresh_interval_seconds() - elapsed)}s "
                            f"(min interval {min_refresh_interval_seconds()}s)."
                        ),
                        overall_last_refreshed=(
                            _LAST_COMPLETED_AT.isoformat() if _LAST_COMPLETED_AT else None
                        ),
                    )
                    return rejected, 429

        state = RefreshJobState(
            refresh_id=str(uuid.uuid4()),
            status="running",
            started_at=datetime.now(UTC).isoformat(),
            progress="Starting source sync…",
            sources=[
                SourceRefreshResult(
                    source_id=sid,
                    label=str(cfg["label"]),
                    status=(
                        "running"
                        if cfg["kind"] in _LIVE_SOURCE_KINDS
                        else "skipped"
                    ),
                    error_summary=(
                        None
                        if cfg["kind"] in _LIVE_SOURCE_KINDS
                        else "Static local import — not re-fetched on refresh"
                    ),
                )
                for sid, cfg in SOURCE_KEYS.items()
            ],
        )
        with _STATE_LOCK:
            _ACTIVE = state

        thread = threading.Thread(
            target=_run_refresh,
            args=(state.refresh_id,),
            name=f"ganesha-refresh-{state.refresh_id[:8]}",
            daemon=True,
        )
        thread.start()
        return state, 202


def _run_refresh(refresh_id: str) -> None:
    global _ACTIVE, _LAST_COMPLETED_AT, _HISTORY
    started = time.monotonic()
    timeout = refresh_timeout_seconds()
    session = SessionLocal()
    created_ids: list[str] = []
    try:
        with _STATE_LOCK:
            state = _ACTIVE
        if state is None or state.refresh_id != refresh_id:
            return

        # --- simplifyjobs (live github sync; closes missing via sync) ---
        _set_progress(state, "Syncing SimplifyJobs…")
        sj = next(s for s in state.sources if s.source_id == "simplifyjobs")
        t0 = time.monotonic()
        try:
            if time.monotonic() - started > timeout:
                raise TimeoutError("Refresh exceeded timeout before SimplifyJobs sync")
            result = sync_simplifyjobs(session, dry_run=False)
            session.commit()
            sj.status = "success"
            sj.fetched = result.parsed
            sj.new = result.created
            sj.updated = result.updated
            sj.closed = result.closed
            if result.errors:
                sj.errors = [str(e) for e in result.errors[:5]]
                sj.error_summary = f"{len(result.errors)} row error(s); sync continued"
            sj.duration_ms = int((time.monotonic() - t0) * 1000)
            sj.last_sync = datetime.now(UTC).isoformat()
            created_ids = list(result.created_ids or [])
        except SimplifyJobsFetchError as exc:
            session.rollback()
            sj.status = "failed"
            sj.error_summary = str(exc)
            sj.duration_ms = int((time.monotonic() - t0) * 1000)
        except Exception as exc:
            session.rollback()
            sj.status = "failed"
            sj.error_summary = f"{exc.__class__.__name__}: {exc}"[:240]
            sj.duration_ms = int((time.monotonic() - t0) * 1000)

        # --- company boards (Greenhouse / Lever / Ashby public APIs) ---
        _set_progress(state, "Syncing company boards…")
        cb = next(s for s in state.sources if s.source_id == "company_boards")
        t1 = time.monotonic()
        try:
            if time.monotonic() - started > timeout:
                raise TimeoutError("Refresh exceeded timeout before company board sync")
            board_result = sync_company_boards(session, dry_run=False)
            session.commit()
            cb.status = "success"
            cb.companies_checked = board_result.companies_checked
            cb.fetched = board_result.fetched
            cb.new = board_result.new
            cb.updated = board_result.updated
            cb.closed = board_result.closed
            if board_result.errors:
                cb.errors = [str(e) for e in board_result.errors[:5]]
                cb.error_summary = (
                    f"{len(board_result.errors)} board/row error(s); sync continued"
                )
            cb.duration_ms = int((time.monotonic() - t1) * 1000)
            cb.last_sync = datetime.now(UTC).isoformat()
            created_ids.extend(board_result.created_ids or [])
        except Exception as exc:
            session.rollback()
            cb.status = "failed"
            cb.error_summary = f"{exc.__class__.__name__}: {exc}"[:240]
            cb.duration_ms = int((time.monotonic() - t1) * 1000)

        for src in state.sources:
            if src.source_id in ("simplifyjobs", "company_boards"):
                continue
            src.status = "skipped"
            last = _last_sync_at(session, src.source_id)
            src.last_sync = last.isoformat() if last else None

        _set_progress(state, "Backfilling first_seen metadata…")
        _backfill_first_seen(session)
        session.commit()

        fit_n = 0
        if created_ids:
            _set_progress(state, f"Assessing fit for {len(created_ids)} new job(s)…")
            fit_n = _assess_new_jobs(session, created_ids)
            session.commit()
        state.fit_assessed = fit_n

        failed = sj.status == "failed" or cb.status == "failed"
        state.status = "failed" if failed else "completed"
        state.message = (
            "One or more sources failed; see per-source results."
            if failed
            else "Refresh complete."
        )
        state.finished_at = datetime.now(UTC).isoformat()
        state.progress = "Done"
        state.overall_last_refreshed = state.finished_at

        record_source_sync(
            session,
            source_id="feed_refresh",
            payload={
                "refresh_id": state.refresh_id,
                "status": state.status,
                "sources": [asdict(s) for s in state.sources],
                "fit_assessed": fit_n,
            },
        )
        session.commit()

        with _STATE_LOCK:
            _LAST_COMPLETED_AT = datetime.now(UTC)
            _HISTORY.append(state)
            if len(_HISTORY) > 20:
                del _HISTORY[:-20]
            _ACTIVE = None
    except Exception as exc:
        session.rollback()
        with _STATE_LOCK:
            if _ACTIVE and _ACTIVE.refresh_id == refresh_id:
                _ACTIVE.status = "failed"
                _ACTIVE.message = f"{exc.__class__.__name__}: {exc}"[:240]
                _ACTIVE.finished_at = datetime.now(UTC).isoformat()
                _ACTIVE.progress = "Failed"
                _HISTORY.append(_ACTIVE)
                _ACTIVE = None
    finally:
        session.close()


def _set_progress(state: RefreshJobState, msg: str) -> None:
    with _STATE_LOCK:
        state.progress = msg


def _source_status_snapshot(session: Session) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for source_id, cfg in SOURCE_KEYS.items():
        last = _last_sync_at(session, source_id)
        # Prefer freshest signal: audit sync_completed OR max scraped_at (Python clock).
        max_scraped = None
        if source_id in ("simplifyjobs", "company_boards"):
            from sqlalchemy import func

            max_scraped = (
                session.query(func.max(JobPosting.scraped_at))
                .filter(JobPosting.source_name.in_(cfg["source_names"]))
                .scalar()
            )
        candidates = [t for t in (last, max_scraped) if t is not None]
        effective = max(candidates) if candidates else None
        age_hours = None
        is_stale = True
        if effective is not None:
            # SQLite may return naive datetimes; normalize before age math.
            if effective.tzinfo is None:
                effective = effective.replace(tzinfo=UTC)
            else:
                effective = effective.astimezone(UTC)
            age_hours = (datetime.now(UTC) - effective).total_seconds() / 3600.0
            is_stale = age_hours > stale_threshold_hours()
        if source_id in ("simplifyjobs", "company_boards"):
            status = "live (manual / dashboard refresh)"
        else:
            status = "static local import"
        out.append(
            {
                "source_id": source_id,
                "label": cfg["label"],
                "kind": cfg["kind"],
                "last_sync": effective.isoformat() if effective else None,
                "age_hours": round(age_hours, 2) if age_hours is not None else None,
                "is_stale": is_stale,
                "status": status,
            }
        )
    return out


def _overall_last_refreshed(
    session: Session, memory_last: datetime | None
) -> str | None:
    candidates: list[datetime] = []
    if memory_last is not None:
        candidates.append(memory_last)
    row = (
        session.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "job_source",
            AuditEvent.entity_id.in_(("simplifyjobs", "feed_refresh")),
            AuditEvent.event_type == "sync_completed",
        )
        .order_by(AuditEvent.created_at.desc())
        .first()
    )
    if row and row.created_at:
        candidates.append(row.created_at)
    from sqlalchemy import func

    max_scraped = (
        session.query(func.max(JobPosting.scraped_at))
        .filter(JobPosting.source_name == "simplifyjobs")
        .scalar()
    )
    if max_scraped is not None:
        candidates.append(max_scraped)
    if not candidates:
        return None
    return max(candidates).isoformat()


def _backfill_first_seen(session: Session) -> int:
    """Ensure discovered_at / first_seen metadata exists; never invents posted_at."""
    n = 0
    # Cap scan; prefer recently scraped rows where clock skew is most likely.
    candidates = (
        session.query(JobPosting)
        .order_by(JobPosting.scraped_at.desc().nullslast())
        .limit(5000)
        .all()
    )
    for job in candidates:
        if ensure_first_seen_metadata(job):
            session.add(job)
            n += 1
    if n:
        session.flush()
    return n


def _assess_new_jobs(session: Session, job_ids: list[str]) -> int:
    from packages.core.services.evidence_fit_service import assess_and_store

    n = 0
    for jid in job_ids:
        job = session.query(JobPosting).filter(JobPosting.id == jid).one_or_none()
        if job is None:
            continue
        eligibility = (job.intake_metadata or {}).get("eligibility") or {}
        if eligibility.get("result") == ELIGIBILITY_EXCLUDED:
            continue
        try:
            assess_and_store(session, job)
            n += 1
        except Exception:
            continue
    return n


def reset_refresh_state_for_tests() -> None:
    """Test helper: clear process-local refresh state."""
    global _ACTIVE, _HISTORY, _LAST_COMPLETED_AT
    with _LOCK:
        with _STATE_LOCK:
            _ACTIVE = None
            _HISTORY.clear()
            _LAST_COMPLETED_AT = None


def scheduled_refresh_docs_path() -> Path:
    return Path(__file__).resolve().parents[3] / "docs" / "job-feed-refresh.md"
