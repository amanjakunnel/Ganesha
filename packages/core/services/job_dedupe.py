from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

from sqlalchemy.orm import Session

from packages.core.domain.models import JobPosting, make_dedupe_key, make_description_hash
from packages.core.services.company_normalize import companies_likely_same


@dataclass
class DedupeResult:
    is_duplicate: bool
    existing_id: str | None = None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _normalize_apply_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    parsed = urlparse(u)
    # Drop common tracking query params for matching
    return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))


def _first_match(
    session: Session,
    *criterion: Any,
    warning_key: str,
) -> tuple[JobPosting | None, str | None]:
    """Return earliest match; never raise MultipleResultsFound on ambiguous rows."""
    matches = (
        session.query(JobPosting)
        .filter(*criterion)
        .order_by(JobPosting.created_at.asc(), JobPosting.id.asc())
        .limit(2)
        .all()
    )
    if not matches:
        return None, None
    warning = f"duplicate_match:{warning_key}" if len(matches) > 1 else None
    return matches[0], warning


def find_duplicate_job(session: Session, data: dict[str, Any]) -> DedupeResult:
    """Conservative duplicate detection with explainable reasons."""
    reasons: list[str] = []
    warnings: list[str] = []

    import_key = data.get("source_import_key")
    source_name = data.get("source_name")
    board_sources = frozenset({"greenhouse", "lever", "ashby", "board_api"})
    if import_key:
        by_key, warn = _first_match(
            session,
            JobPosting.source_import_key == import_key,
            warning_key=f"source_import_key:{import_key}",
        )
        if warn:
            warnings.append(warn)
        if by_key:
            return DedupeResult(True, by_key.id, ["source_import_key"], warnings)
        # Board import keys are authoritative: skip fuzzy URL/hash scans that
        # otherwise load every apply URL for each new board row (O(n²)).
        if (source_name or "") in board_sources:
            external_id = data.get("external_id")
            if external_id and source_name:
                by_ext, warn = _first_match(
                    session,
                    JobPosting.external_id == external_id,
                    JobPosting.source_name == source_name,
                    warning_key=f"external_id:{source_name}:{external_id}",
                )
                if warn:
                    warnings.append(warn)
                if by_ext:
                    return DedupeResult(
                        True, by_ext.id, ["external_id+source_name"], warnings
                    )
            return DedupeResult(False, None, [], warnings)

    external_id = data.get("external_id")
    if external_id and source_name:
        by_ext, warn = _first_match(
            session,
            JobPosting.external_id == external_id,
            JobPosting.source_name == source_name,
            warning_key=f"external_id:{source_name}:{external_id}",
        )
        if warn:
            warnings.append(warn)
        if by_ext:
            return DedupeResult(
                True, by_ext.id, ["external_id+source_name"], warnings
            )

    apply_url = _normalize_apply_url(data.get("company_apply_url") or data.get("canonical_url"))
    title = (data.get("title") or "").lower().strip()
    company = data.get("company") or ""

    if apply_url:
        # Lean projection only — never pull description_text blobs for URL matching.
        from sqlalchemy.orm import load_only

        candidates = (
            session.query(JobPosting)
            .options(
                load_only(
                    JobPosting.id,
                    JobPosting.company_apply_url,
                    JobPosting.title,
                    JobPosting.company_id,
                )
            )
            .filter(JobPosting.company_apply_url.is_not(None))
            .all()
        )
        for c in candidates:
            if _normalize_apply_url(c.company_apply_url) == apply_url:
                if companies_likely_same(
                    company, c.company.canonical_name if c.company else ""
                ) or _title_similar(title, (c.title or "").lower()):
                    return DedupeResult(True, c.id, ["company_apply_url"], warnings)

    dedupe_key = make_dedupe_key(company, data.get("title"), data.get("location"))
    desc_hash = make_description_hash(data.get("description_text") or "")
    by_dedupe, warn = _first_match(
        session,
        (JobPosting.dedupe_key == dedupe_key) | (JobPosting.description_hash == desc_hash),
        warning_key=f"dedupe_key_or_description_hash:{dedupe_key}",
    )
    if warn:
        warnings.append(warn)
    if by_dedupe:
        reasons.append("dedupe_key_or_description_hash")
        return DedupeResult(True, by_dedupe.id, reasons, warnings)

    return DedupeResult(False, None, [], warnings)


def _title_similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if not a_tokens or not b_tokens:
        return False
    overlap = len(a_tokens & b_tokens) / max(len(a_tokens), len(b_tokens))
    return overlap >= 0.6
