from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from packages.core.domain.models import AuditEvent, JobPosting

SOURCE_KEYS = {
    "linkedin_import": {
        "label": "linkedin_import",
        "source_names": ("linkedin", "linkedin_import"),
        "kind": "static_file",
    },
    "symplicity_import": {
        "label": "symplicity_import",
        "source_names": ("symplicity", "symplicity_import"),
        "kind": "static_file",
    },
    "simplifyjobs": {
        "label": "simplifyjobs",
        "source_names": ("simplifyjobs",),
        "kind": "github_sync",
    },
}


def record_source_sync(
    session: Session,
    *,
    source_id: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        AuditEvent(
            entity_type="job_source",
            entity_id=source_id,
            event_type="sync_completed",
            payload=payload,
        )
    )


def _last_sync_at(session: Session, source_id: str) -> datetime | None:
    row = (
        session.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "job_source",
            AuditEvent.entity_id == source_id,
            AuditEvent.event_type == "sync_completed",
        )
        .order_by(AuditEvent.created_at.desc())
        .first()
    )
    return row.created_at if row else None


def _count_jobs(session: Session, source_names: tuple[str, ...]) -> int:
    return (
        session.query(func.count(JobPosting.id))
        .filter(JobPosting.source_name.in_(source_names))
        .scalar()
        or 0
    )


def build_sources_summary(session: Session) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for source_id, cfg in SOURCE_KEYS.items():
        last_sync = _last_sync_at(session, source_id)
        if source_id == "simplifyjobs":
            status = "live (manual CLI sync)" if last_sync else "not synced yet"
        else:
            status = "static local import"
        summaries.append(
            {
                "source_id": source_id,
                "label": cfg["label"],
                "kind": cfg["kind"],
                "job_count": _count_jobs(session, tuple(cfg["source_names"])),
                "last_sync": last_sync.isoformat() if last_sync else None,
                "status": status,
            }
        )
    return summaries
