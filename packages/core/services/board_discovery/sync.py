"""Sync jobs from resolved Greenhouse / Lever / Ashby public boards."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from packages.core.domain.models import AuditEvent, JobPosting
from packages.core.services.board_discovery import ashby, greenhouse, lever
from packages.core.services.board_discovery.company_resolution import (
    resolve_company_boards,
)
from packages.core.services.board_discovery.http_client import BoardHttpClient
from packages.core.services.board_discovery.types import (
    PLATFORM_ASHBY,
    PLATFORM_GREENHOUSE,
    PLATFORM_LEVER,
    BoardFetchResult,
    BoardJob,
    BoardRef,
    UnresolvedCompany,
)
from packages.core.services.job_eligibility import (
    ELIGIBILITY_EXCLUDED,
    ELIGIBILITY_NEEDS_REVIEW,
    attach_eligibility_to_metadata,
    classify_job_eligibility,
)
from packages.core.services.job_intake import upsert_job_from_import
from packages.core.services.source_registry import record_source_sync
from packages.core.settings import settings

FetchFn = Callable[..., BoardFetchResult]


@dataclass
class CompanyBoardSyncResult:
    dry_run: bool = False
    companies_checked: int = 0
    boards_attempted: int = 0
    boards_ok: int = 0
    fetched: int = 0
    new: int = 0
    updated: int = 0
    closed: int = 0
    excluded: int = 0
    needs_review: int = 0
    actionable: int = 0
    errors: list[str] = field(default_factory=list)
    unresolved: list[UnresolvedCompany] = field(default_factory=list)
    created_ids: list[str] = field(default_factory=list)
    etags: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "companies_checked": self.companies_checked,
            "boards_attempted": self.boards_attempted,
            "boards_ok": self.boards_ok,
            "fetched": self.fetched,
            "new": self.new,
            "updated": self.updated,
            "closed": self.closed,
            "excluded": self.excluded,
            "needs_review": self.needs_review,
            "actionable": self.actionable,
            "errors": len(self.errors),
            "unresolved": len(self.unresolved),
        }


_FETCHERS: dict[str, FetchFn] = {
    PLATFORM_GREENHOUSE: greenhouse.fetch_board,
    PLATFORM_LEVER: lever.fetch_board,
    PLATFORM_ASHBY: ashby.fetch_board,
}


def _max_companies(limit: int | None) -> int:
    if limit is not None:
        return max(0, limit)
    return int(
        getattr(settings, "company_board_discovery_max_companies", 25) or 25
    )


def _load_prior_etags(session: Session) -> dict[str, str]:
    row = (
        session.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "job_source",
            AuditEvent.entity_id == "company_boards",
            AuditEvent.event_type == "sync_completed",
        )
        .order_by(AuditEvent.created_at.desc())
        .first()
    )
    if not row or not isinstance(row.payload, dict):
        return {}
    etags = row.payload.get("etags") or {}
    if not isinstance(etags, dict):
        return {}
    return {str(k): str(v) for k, v in etags.items() if v}


def board_job_to_import_dict(
    job: BoardJob,
    *,
    synced_at: datetime,
    apply_eligibility: bool,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "board_discovery": {
            "platform": job.platform,
            "board_token": job.board_token,
            "requisition_id": job.requisition_id,
        }
    }
    data: dict[str, Any] = {
        "title": job.title,
        "company": job.company,
        "description_text": job.description_text,
        "company_apply_url": job.company_apply_url,
        "canonical_url": job.canonical_url,
        "location": job.location,
        "workplace_type": job.workplace_type,
        "employment_type": job.employment_type,
        "external_id": job.external_id,
        "source_import_key": job.source_import_key,
        "scraped_at": synced_at.isoformat(),
        "posted_at": job.posted_at,
        "raw_payload": job.raw_payload,
        "intake_metadata": meta,
        "source_name": job.platform,
    }
    if apply_eligibility:
        eligibility = classify_job_eligibility(
            title=job.title,
            description=job.description_text,
            company_apply_url=job.company_apply_url,
            location=job.location,
            employment_type=job.employment_type,
        )
        data["intake_metadata"] = attach_eligibility_to_metadata(meta, eligibility)
        data["_eligibility_result"] = eligibility.result
    return data


def mark_missing_board_jobs_closed(
    session: Session,
    *,
    board: BoardRef,
    seen_import_keys: set[str],
) -> int:
    """Mark board jobs absent from this board fetch as listing_closed (never delete)."""
    if not seen_import_keys:
        # Empty board can be legitimate; close previously-known jobs for this board.
        pass
    closed = 0
    prefix = f"{board.platform}|{board.board_token}|"
    jobs = (
        session.query(JobPosting)
        .filter(JobPosting.source_name == board.platform)
        .all()
    )
    for job in jobs:
        key = job.source_import_key
        if not key or not key.startswith(prefix):
            continue
        if key in seen_import_keys:
            continue
        meta = dict(job.intake_metadata or {})
        markers = dict(meta.get("simplify_markers") or {})
        if markers.get("listing_closed"):
            continue
        markers["listing_closed"] = True
        meta["simplify_markers"] = markers
        meta["closed_reason"] = "missing_from_board_sync"
        meta["closed_at"] = datetime.now(UTC).isoformat()
        meta["board_discovery"] = {
            **dict(meta.get("board_discovery") or {}),
            "platform": board.platform,
            "board_token": board.board_token,
        }
        eligibility = classify_job_eligibility(
            title=job.title,
            description=job.description_text or "",
            company_apply_url=job.company_apply_url,
            location=job.location,
            employment_type=job.employment_type,
            source_markers=markers,
        )
        meta = attach_eligibility_to_metadata(meta, eligibility)
        job.intake_metadata = meta
        session.add(job)
        closed += 1
    if closed:
        session.flush()
    return closed


def sync_company_boards(
    session: Session,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    csv_text: str | None = None,
    client: BoardHttpClient | None = None,
    prior_etags: dict[str, str] | None = None,
) -> CompanyBoardSyncResult:
    result = CompanyBoardSyncResult(dry_run=dry_run)
    max_companies = _max_companies(limit)
    synced_at = datetime.now(UTC)

    resolution = resolve_company_boards(
        session, limit=max_companies, csv_text=csv_text
    )
    result.companies_checked = len(resolution.companies)
    result.unresolved = list(resolution.unresolved)
    if resolution.sheet_error:
        result.errors.append(f"referral_sheet: {resolution.sheet_error}")
        return result

    etags = dict(prior_etags if prior_etags is not None else _load_prior_etags(session))
    own_client = client is None
    if client is None:
        client = BoardHttpClient()

    try:
        for board in resolution.boards:
            result.boards_attempted += 1
            fetcher = _FETCHERS.get(board.platform)
            if fetcher is None:
                result.errors.append(f"{board.key}: unsupported platform")
                continue
            fetch = fetcher(client, board, etag=etags.get(board.key))
            if not fetch.ok:
                # Unverified boards are treated as unresolved for that company token.
                result.errors.append(
                    f"{board.platform}/{board.board_token}: {fetch.error or 'fetch failed'}"
                )
                result.unresolved.append(
                    UnresolvedCompany(
                        company=board.company,
                        reason=f"board_verify_failed:{fetch.error or 'error'}",
                    )
                )
                continue

            result.boards_ok += 1
            if fetch.etag:
                result.etags[board.key] = fetch.etag
                etags[board.key] = fetch.etag

            if fetch.not_modified:
                result.notes.append(f"{board.key}: not modified (304)")
                continue

            result.fetched += len(fetch.jobs)
            seen_keys: set[str] = set()
            for job in fetch.jobs:
                seen_keys.add(job.source_import_key)
                # Eligibility only for rows we expect to create; updates skip reclassify.
                # Pre-check duplicate cheaply via import key for eligibility gating.
                existing = (
                    session.query(JobPosting.id)
                    .filter(JobPosting.source_import_key == job.source_import_key)
                    .one_or_none()
                )
                is_newish = existing is None
                data = board_job_to_import_dict(
                    job,
                    synced_at=synced_at,
                    apply_eligibility=is_newish,
                )
                eligibility_result = data.pop("_eligibility_result", None)
                if dry_run:
                    if eligibility_result == ELIGIBILITY_EXCLUDED:
                        result.excluded += 1
                    elif eligibility_result == ELIGIBILITY_NEEDS_REVIEW:
                        result.needs_review += 1
                    elif eligibility_result:
                        result.actionable += 1
                    if is_newish:
                        result.new += 1
                    else:
                        result.updated += 1
                    continue
                try:
                    job_id, created = upsert_job_from_import(
                        session,
                        data,
                        source_name=job.platform,
                        source_type="board_api",
                        legacy_source="board_api",
                        reclassify_on_update=False,
                    )
                    if created:
                        result.new += 1
                        result.created_ids.append(job_id)
                        if eligibility_result == ELIGIBILITY_EXCLUDED:
                            result.excluded += 1
                        elif eligibility_result == ELIGIBILITY_NEEDS_REVIEW:
                            result.needs_review += 1
                        else:
                            result.actionable += 1
                    else:
                        result.updated += 1
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(
                        f"{job.company} / {job.title}: {exc.__class__.__name__}"
                    )

            if not dry_run:
                result.closed += mark_missing_board_jobs_closed(
                    session, board=board, seen_import_keys=seen_keys
                )
    finally:
        if own_client and client is not None:
            client.close()

    # Companies with no board candidate already in unresolved; companies whose
    # only boards failed also appended above.
    if not dry_run and result.boards_attempted:
        record_source_sync(
            session,
            source_id="company_boards",
            payload={
                **result.to_summary(),
                "synced_at": synced_at.isoformat(),
                "etags": result.etags,
                "unresolved_samples": [
                    {"company": u.company, "reason": u.reason}
                    for u in result.unresolved[:25]
                ],
                "error_samples": result.errors[:10],
            },
        )
    return result
