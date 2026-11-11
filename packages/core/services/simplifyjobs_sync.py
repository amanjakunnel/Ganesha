from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from packages.core.domain.models import JobPosting
from packages.core.services.importers.simplifyjobs_github import (
    SIMPLIFYJOBS_README_RAW,
    SIMPLIFYJOBS_REPO_URL,
    parse_simplifyjobs_readme,
    parsed_row_to_import_dict,
)
from packages.core.services.job_eligibility import (
    ELIGIBILITY_EXCLUDED,
    ELIGIBILITY_NEEDS_REVIEW,
    attach_eligibility_to_metadata,
    classify_job_eligibility,
)
from packages.core.services.job_intake import upsert_job_from_import
from packages.core.services.source_registry import record_source_sync


class SimplifyJobsFetchError(RuntimeError):
    pass


@dataclass
class SimplifyJobsSyncResult:
    fetched: bool = False
    parsed: int = 0
    created: int = 0
    updated: int = 0
    duplicates: int = 0
    excluded: int = 0
    needs_review: int = 0
    actionable: int = 0
    closed: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False
    seen_import_keys: list[str] = field(default_factory=list)
    created_ids: list[str] = field(default_factory=list)


def fetch_simplifyjobs_readme(
    *,
    url: str = SIMPLIFYJOBS_README_RAW,
    timeout: float = 30.0,
) -> str:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SimplifyJobsFetchError(
            "Could not fetch SimplifyJobs README from GitHub. "
            "Check network access and try again."
        ) from exc
    if not response.text.strip():
        raise SimplifyJobsFetchError("SimplifyJobs README response was empty.")
    return response.text


def sync_simplifyjobs(
    session: Session,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    readme_text: str | None = None,
) -> SimplifyJobsSyncResult:
    result = SimplifyJobsSyncResult(dry_run=dry_run)
    synced_at = datetime.now(UTC)

    if readme_text is None:
        readme_text = fetch_simplifyjobs_readme()
    result.fetched = True

    rows = parse_simplifyjobs_readme(readme_text, synced_at=synced_at)
    if limit is not None:
        rows = rows[:limit]
    result.parsed = len(rows)

    for row in rows:
        try:
            data = parsed_row_to_import_dict(row, synced_at=synced_at)
            import_key = data.get("source_import_key")
            if import_key:
                result.seen_import_keys.append(str(import_key))
            markers = (data.get("intake_metadata") or {}).get("simplify_markers") or {}
            eligibility = classify_job_eligibility(
                title=data["title"],
                description=data["description_text"],
                company_apply_url=data.get("company_apply_url"),
                location=data.get("location"),
                source_markers=markers,
                incomplete_row=not data["title"] or not data["company"],
            )
            meta = dict(data.get("intake_metadata") or {})
            data["intake_metadata"] = attach_eligibility_to_metadata(meta, eligibility)

            if dry_run:
                _tally_eligibility(result, eligibility.result)
                continue

            job_id, created = upsert_job_from_import(
                session,
                data,
                source_name="simplifyjobs",
                source_type="github_sync",
                legacy_source="github_sync",
            )
            _tally_eligibility(result, eligibility.result)
            if created:
                result.created += 1
                result.created_ids.append(job_id)
            else:
                result.duplicates += 1
                result.updated += 1
        except Exception as exc:
            result.errors.append(
                f"{row.company} / {row.title}: {exc.__class__.__name__}"
            )

    if not dry_run:
        result.closed = mark_missing_simplifyjobs_closed(
            session, seen_import_keys=set(result.seen_import_keys)
        )
        record_source_sync(
            session,
            source_id="simplifyjobs",
            payload={
                "repo": SIMPLIFYJOBS_REPO_URL,
                "parsed": result.parsed,
                "created": result.created,
                "updated": result.updated,
                "duplicates": result.duplicates,
                "closed": result.closed,
                "excluded": result.excluded,
                "needs_review": result.needs_review,
                "actionable": result.actionable,
                "errors": len(result.errors),
                "synced_at": synced_at.isoformat(),
            },
        )
    return result


def _tally_eligibility(result: SimplifyJobsSyncResult, eligibility_result: str) -> None:
    if eligibility_result == ELIGIBILITY_EXCLUDED:
        result.excluded += 1
    elif eligibility_result == ELIGIBILITY_NEEDS_REVIEW:
        result.needs_review += 1
    else:
        result.actionable += 1


def mark_missing_simplifyjobs_closed(
    session: Session,
    *,
    seen_import_keys: set[str],
) -> int:
    """Mark SimplifyJobs postings absent from the latest sync as listing_closed.

    Does not delete rows. Uses the existing listing_closed eligibility convention.
    """
    if not seen_import_keys:
        return 0
    closed = 0
    jobs = (
        session.query(JobPosting)
        .filter(JobPosting.source_name == "simplifyjobs")
        .all()
    )
    for job in jobs:
        key = job.source_import_key
        if not key or key in seen_import_keys:
            continue
        meta = dict(job.intake_metadata or {})
        markers = dict(meta.get("simplify_markers") or {})
        if markers.get("listing_closed"):
            continue
        markers["listing_closed"] = True
        meta["simplify_markers"] = markers
        meta["closed_reason"] = "missing_from_source_sync"
        meta["closed_at"] = datetime.now(UTC).isoformat()
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
