"""One-way Simplify Job Tracker CSV import (manual export — not live sync)."""

from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from sqlalchemy.orm import Session

from packages.core.domain.models import (
    AuditEvent,
    JobApplication,
    JobPosting,
    SimplifyHistoryRecord,
    hash_text,
    make_uuid,
)

SOURCE_NAME = "simplify"
SOURCE_TYPE = "tracker_csv_export"

# Explicit mapping — SAVED/viewed must never become applied.
STATUS_MAP: dict[str, str | None] = {
    "APPLIED": "applied",
    "SAVED": "saved_only",
    "SCREEN": "screen",
    "INTERVIEW": "interview",
    "OFFER": "offer",
    "REJECTED": "rejected",
    "WITHDRAWN": "withdrawn",
    "GHOSTED": "ghosted",
    "ACCEPTED": "accepted",
}


@dataclass
class SimplifyImportReport:
    batch_id: str
    path: str
    headers: list[str] = field(default_factory=list)
    rows_read: int = 0
    duplicates_collapsed: int = 0
    matched_existing_jobs: int = 0
    new_records: int = 0
    already_imported: int = 0
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)
    applied_like: int = 0
    saved_only: int = 0
    unknown_status: int = 0
    dry_run: bool = True
    archive_path: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "path": self.path,
            "headers": self.headers,
            "rows_read": self.rows_read,
            "duplicates_collapsed": self.duplicates_collapsed,
            "matched_existing_jobs": self.matched_existing_jobs,
            "new_records": self.new_records,
            "already_imported": self.already_imported,
            "ambiguous_count": len(self.ambiguous),
            "ambiguous": self.ambiguous[:25],
            "status_counts": self.status_counts,
            "applied_like": self.applied_like,
            "saved_only": self.saved_only,
            "unknown_status": self.unknown_status,
            "dry_run": self.dry_run,
            "archive_path": self.archive_path,
            "status_mapping": {k: v for k, v in STATUS_MAP.items()},
            "errors": self.errors,
        }


def canonical_job_url(url: str | None) -> str | None:
    if not url or not str(url).strip():
        return None
    parsed = urlparse(str(url).strip())
    if parsed.scheme not in {"http", "https"}:
        return None
    # Drop tracking query/fragment for dedupe; keep path.
    clean = parsed._replace(query="", fragment="")
    return urlunparse(clean).rstrip("/")


def map_simplify_status(raw: str | None) -> tuple[str, str | None]:
    key = (raw or "").strip().upper()
    if not key:
        return "", None
    mapped = STATUS_MAP.get(key)
    if mapped is None and key not in STATUS_MAP:
        return key.lower(), None  # unknown stays unknown
    if key == "SAVED":
        return "saved_only", "saved_only"
    return key.lower(), mapped


def _archive_copy(src: Path, archive_dir: Path, batch_id: str) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / f"{batch_id}_{src.name}"
    if not dest.exists():
        shutil.copy2(src, dest)
    return dest


def _build_url_index(session: Session) -> dict[str, list[JobPosting]]:
    index: dict[str, list[JobPosting]] = {}
    for job in session.query(JobPosting).yield_per(200):
        for raw in (job.company_apply_url, job.canonical_url):
            key = canonical_job_url(raw)
            if not key:
                continue
            index.setdefault(key, [])
            if job not in index[key]:
                index[key].append(job)
    return index


def _match_job(
    url_index: dict[str, list[JobPosting]], url: str | None
) -> tuple[JobPosting | None, str | None]:
    """Match by canonical URL first; company+title alone is never enough."""
    if not url:
        return None, None
    hits = url_index.get(url) or []
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        return None, "multiple_jobs_same_url"
    return None, None


def import_simplify_tracker_csv(
    session: Session,
    path: str | Path,
    *,
    dry_run: bool = True,
    archive_dir: Path | None = None,
) -> SimplifyImportReport:
    src = Path(path)
    batch_id = make_uuid()
    report = SimplifyImportReport(batch_id=batch_id, path=str(src), dry_run=dry_run)
    if not src.is_file():
        report.errors.append(f"file not found: {src}")
        return report

    if archive_dir is None:
        archive_dir = Path(__file__).resolve().parents[4] / "data" / "import_archive"
    report.archive_path = str(_archive_copy(src, archive_dir, batch_id))

    with src.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        report.headers = list(reader.fieldnames or [])
        rows = list(reader)
    report.rows_read = len(rows)

    # Collapse duplicate rows by canonical URL + mapped status + applied date.
    collapsed: dict[str, dict[str, Any]] = {}
    for idx, row in enumerate(rows, start=2):
        url = canonical_job_url(row.get("Job URL"))
        status_raw, status_mapped = map_simplify_status(row.get("Status"))
        applied = (row.get("Applied Date") or "").strip() or None
        key = hash_text(
            f"simplify|{url or ''}|{status_mapped or status_raw}|{applied}|"
            f"{(row.get('Company Name') or '').strip().lower()}|"
            f"{(row.get('Job Title') or '').strip().lower()}"
        )
        if key in collapsed:
            report.duplicates_collapsed += 1
            continue
        location = (row.get("Location") or "").strip() or None
        if location and len(location) > 500:
            location = location[:497] + "…"
        company = (row.get("Company Name") or "").strip()[:255]
        role = (row.get("Job Title") or "").strip()[:512]
        notes = (row.get("Notes") or "").strip() or None
        collapsed[key] = {
            "row_index": idx,
            "company": company,
            "role": role,
            "location": location,
            "job_url": url,
            "job_url_raw": ((row.get("Job URL") or "").strip() or None),
            "applied_date": applied,
            "status_raw": (row.get("Status") or "").strip()[:64],
            "status_mapped": status_mapped,
            "archived": (row.get("Archived") or "").strip(),
            "notes": notes,
            "job_type": ((row.get("job_type") or "").strip() or None),
            "source_import_key": key,
        }

    url_index = _build_url_index(session)

    for item in collapsed.values():
        raw = (item["status_raw"] or "").upper()
        report.status_counts[raw or "(blank)"] = (
            report.status_counts.get(raw or "(blank)", 0) + 1
        )
        if item["status_mapped"] == "applied":
            report.applied_like += 1
        elif item["status_mapped"] == "saved_only":
            report.saved_only += 1
        elif item["status_mapped"] is None:
            report.unknown_status += 1

        existing = (
            session.query(SimplifyHistoryRecord)
            .filter(SimplifyHistoryRecord.source_import_key == item["source_import_key"])
            .one_or_none()
        )
        if existing:
            report.already_imported += 1
            continue

        job, ambiguous_reason = _match_job(url_index, item["job_url"])
        if ambiguous_reason:
            report.ambiguous.append(
                {
                    "row_index": item["row_index"],
                    "company": item["company"],
                    "role": item["role"],
                    "url": item["job_url"],
                    "reason": ambiguous_reason,
                }
            )
        if job is not None:
            report.matched_existing_jobs += 1

        if dry_run:
            report.new_records += 1
            continue

        record = SimplifyHistoryRecord(
            id=make_uuid(),
            batch_id=batch_id,
            row_index=item["row_index"],
            company_name=item["company"] or "Unknown",
            role_title=item["role"] or "Unknown",
            location=item["location"],
            job_url=item["job_url"],
            job_url_raw=item["job_url_raw"],
            applied_date_text=item["applied_date"],
            status_raw=item["status_raw"],
            status_mapped=item["status_mapped"],
            job_type=item["job_type"],
            notes=item["notes"],
            job_posting_id=job.id if job else None,
            source_name=SOURCE_NAME,
            source_type=SOURCE_TYPE,
            source_import_key=item["source_import_key"],
            payload_json={
                "archived": item["archived"],
                "batch_id": batch_id,
                "row_index": item["row_index"],
            },
        )
        session.add(record)
        report.new_records += 1

        # Link JobApplication only for applied-like statuses on a matched job.
        # Never mark saved_only as submitted/applied.
        if job is not None and item["status_mapped"] == "applied":
            app = (
                session.query(JobApplication)
                .filter(JobApplication.job_posting_id == job.id)
                .one_or_none()
            )
            if app is None:
                app = JobApplication(
                    id=make_uuid(),
                    job_posting_id=job.id,
                    status="submitted",
                    submitted_at=datetime.now(UTC),
                    notes=f"Imported from Simplify tracker batch {batch_id}",
                )
                session.add(app)
            elif app.status == "draft":
                # Merge policy: do not downgrade richer states; upgrade draft only.
                app.status = "submitted"
                app.submitted_at = app.submitted_at or datetime.now(UTC)
                note = app.notes or ""
                if "Simplify tracker" not in note:
                    app.notes = (
                        (note + " | " if note else "")
                        + f"Imported from Simplify tracker batch {batch_id}"
                    ).strip(" |")

        session.add(
            AuditEvent(
                entity_type="simplify_history",
                entity_id=record.id,
                event_type="simplify_tracker_row_imported",
                payload={
                    "batch_id": batch_id,
                    "status_mapped": item["status_mapped"],
                    "job_posting_id": job.id if job else None,
                },
            )
        )

    if not dry_run:
        session.flush()
    return report


def previously_applied_for_job(session: Session, job: JobPosting) -> dict[str, Any] | None:
    """Return a warning payload if Simplify/Ganesha history says already applied."""
    urls = {
        u
        for u in (
            canonical_job_url(job.company_apply_url),
            canonical_job_url(job.canonical_url),
        )
        if u
    }
    q = session.query(SimplifyHistoryRecord).filter(
        SimplifyHistoryRecord.status_mapped == "applied"
    )
    hit = None
    if urls:
        for row in q.limit(5000):
            if row.job_posting_id == job.id or (
                row.job_url and row.job_url in urls
            ):
                hit = row
                break
    if hit is None:
        app = (
            session.query(JobApplication)
            .filter(
                JobApplication.job_posting_id == job.id,
                JobApplication.status.in_(
                    ["submitted", "interview", "rejected", "withdrawn"]
                ),
            )
            .one_or_none()
        )
        if app is None:
            return None
        return {
            "source": "ganesha",
            "status": app.status,
            "requires_explicit_continue": True,
            "message": "Ganesha already has an application record for this job.",
        }
    return {
        "source": "simplify",
        "status": hit.status_mapped,
        "applied_date": hit.applied_date_text,
        "company": hit.company_name,
        "role": hit.role_title,
        "requires_explicit_continue": True,
        "message": (
            "Simplify history shows a prior application for this job. "
            "Continue only if you intentionally want another attempt."
        ),
    }
