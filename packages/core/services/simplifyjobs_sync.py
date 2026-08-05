from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

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
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


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
            markers = (data.get("intake_metadata") or {}).get("simplify_markers") or {}
            eligibility = classify_job_eligibility(
                title=data["title"],
                description=data["description_text"],
                company_apply_url=data.get("company_apply_url"),
                source_markers=markers,
                incomplete_row=not data["title"] or not data["company"],
            )
            meta = dict(data.get("intake_metadata") or {})
            data["intake_metadata"] = attach_eligibility_to_metadata(meta, eligibility)

            if dry_run:
                _tally_eligibility(result, eligibility.result)
                continue

            _job_id, created = upsert_job_from_import(
                session,
                data,
                source_name="simplifyjobs",
                source_type="github_sync",
                legacy_source="github_sync",
            )
            _tally_eligibility(result, eligibility.result)
            if created:
                result.created += 1
            else:
                result.duplicates += 1
                result.updated += 1
        except Exception as exc:
            result.errors.append(
                f"{row.company} / {row.title}: {exc.__class__.__name__}"
            )

    if not dry_run:
        record_source_sync(
            session,
            source_id="simplifyjobs",
            payload={
                "repo": SIMPLIFYJOBS_REPO_URL,
                "parsed": result.parsed,
                "created": result.created,
                "updated": result.updated,
                "duplicates": result.duplicates,
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
