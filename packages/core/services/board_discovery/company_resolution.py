"""Resolve referral companies to Greenhouse / Lever / Ashby board tokens.

Board tokens are derived from existing JobPosting apply/canonical URLs.
Unique company names come from the referral contacts sheet adapter.
Contact PII is never logged or attached to jobs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy.orm import Session

from packages.core.domain.models import JobPosting
from packages.core.services.board_discovery.ashby import extract_ashby_board_name
from packages.core.services.board_discovery.greenhouse import extract_greenhouse_token
from packages.core.services.board_discovery.lever import extract_lever_site
from packages.core.services.board_discovery.types import (
    PLATFORM_ASHBY,
    PLATFORM_GREENHOUSE,
    PLATFORM_LEVER,
    BoardRef,
    UnresolvedCompany,
)
from packages.core.services.company_normalize import normalize_company_key
from packages.core.services.importers.referral_contacts_sheet import (
    fetch_referral_sheet_csv,
    unique_companies_from_sheet_csv,
)


@dataclass
class BoardCandidate:
    platform: str
    board_token: str
    region: str | None
    company_name: str
    source_url: str


@dataclass
class ResolutionResult:
    companies: list[str] = field(default_factory=list)
    boards: list[BoardRef] = field(default_factory=list)
    unresolved: list[UnresolvedCompany] = field(default_factory=list)
    sheet_error: str | None = None


def parse_board_from_url(url: str | None) -> tuple[str, str, str | None] | None:
    """Return (platform, token, region) or None."""
    if not url:
        return None
    gh = extract_greenhouse_token(url)
    if gh:
        return PLATFORM_GREENHOUSE, gh, None
    lever = extract_lever_site(url)
    if lever:
        site, region = lever
        return PLATFORM_LEVER, site, region
    ashby = extract_ashby_board_name(url)
    if ashby:
        return PLATFORM_ASHBY, ashby, None
    return None


def collect_board_candidates_from_jobs(
    session: Session,
    *,
    limit_scan: int = 8000,
) -> list[BoardCandidate]:
    # Only pull URL/company columns — full JobPosting rows include huge descriptions
    # and have crashed local Postgres under memory pressure.
    from packages.core.domain.models import Company

    rows = (
        session.query(
            JobPosting.company_apply_url,
            JobPosting.canonical_url,
            Company.canonical_name,
        )
        .outerjoin(Company, Company.id == JobPosting.company_id)
        .order_by(JobPosting.scraped_at.desc().nullslast())
        .limit(limit_scan)
        .all()
    )
    out: list[BoardCandidate] = []
    seen: set[str] = set()
    for apply_url, canonical_url, company_name in rows:
        company_name = company_name or ""
        for url in (apply_url, canonical_url):
            parsed = parse_board_from_url(url)
            if not parsed:
                continue
            platform, token, region = parsed
            dedupe = f"{platform}|{region or 'global'}|{token}|{normalize_company_key(company_name)}"
            if dedupe in seen:
                continue
            seen.add(dedupe)
            out.append(
                BoardCandidate(
                    platform=platform,
                    board_token=token,
                    region=region,
                    company_name=company_name,
                    source_url=url or "",
                )
            )
    return out


def _company_keys_match(a: str, b: str) -> bool:
    """Strict company match for board attribution (avoid substring false positives)."""
    ka, kb = normalize_company_key(a), normalize_company_key(b)
    if not ka or not kb:
        return False
    return ka == kb


def resolve_boards_for_companies(
    companies: Iterable[str],
    candidates: Iterable[BoardCandidate],
) -> tuple[list[BoardRef], list[UnresolvedCompany]]:
    cand_list = list(candidates)
    boards: list[BoardRef] = []
    unresolved: list[UnresolvedCompany] = []
    board_keys: set[str] = set()

    for company in companies:
        matches = [
            c
            for c in cand_list
            if c.company_name and _company_keys_match(company, c.company_name)
        ]
        if not matches:
            unresolved.append(
                UnresolvedCompany(
                    company=company,
                    reason="no_board_url_in_job_postings",
                )
            )
            continue
        # Prefer stable platform order; keep all unique boards for the company.
        added = 0
        for match in sorted(matches, key=lambda m: (m.platform, m.board_token)):
            ref = BoardRef(
                platform=match.platform,
                board_token=match.board_token,
                company=company,
                region=match.region,
            )
            if ref.key in board_keys:
                continue
            board_keys.add(ref.key)
            boards.append(ref)
            added += 1
        if added == 0:
            unresolved.append(
                UnresolvedCompany(
                    company=company,
                    reason="no_board_url_in_job_postings",
                )
            )
    return boards, unresolved


def load_pilot_companies(
    *,
    limit: int,
    csv_text: str | None = None,
) -> tuple[list[str], str | None]:
    """Return (companies, sheet_error). Never returns contact rows."""
    if csv_text is None:
        fetched = fetch_referral_sheet_csv()
        if not fetched.ok or not fetched.csv_text:
            return [], fetched.error or "sheet unavailable"
        csv_text = fetched.csv_text
    companies = unique_companies_from_sheet_csv(csv_text)[: max(0, limit)]
    return companies, None


def resolve_company_boards(
    session: Session,
    *,
    limit: int,
    csv_text: str | None = None,
) -> ResolutionResult:
    """Build a pilot board list from (a) job-URL tokens and (b) referral companies.

    Caps total boards at ``limit`` (default 25). Job-derived boards are preferred
    because their tokens are already proven live apply URLs. Referral companies
    without a matching board stay in ``unresolved`` and are skipped.
    """
    companies, sheet_error = load_pilot_companies(limit=limit, csv_text=csv_text)
    result = ResolutionResult(companies=companies, sheet_error=sheet_error)
    candidates = collect_board_candidates_from_jobs(session)

    # (a) Boards already evidenced by job postings — unique by platform|region|token.
    job_boards: list[BoardRef] = []
    seen: set[str] = set()
    for cand in candidates:
        ref = BoardRef(
            platform=cand.platform,
            board_token=cand.board_token,
            company=cand.company_name or cand.board_token,
            region=cand.region,
        )
        if ref.key in seen:
            continue
        seen.add(ref.key)
        job_boards.append(ref)

    # (b) Referral sheet companies that match a job-derived board token/company.
    sheet_boards: list[BoardRef] = []
    unresolved: list[UnresolvedCompany] = []
    if companies:
        sheet_boards, unresolved = resolve_boards_for_companies(companies, candidates)

    merged: list[BoardRef] = []
    merged_keys: set[str] = set()
    for ref in [*job_boards, *sheet_boards]:
        if ref.key in merged_keys:
            continue
        merged_keys.add(ref.key)
        merged.append(ref)
        if len(merged) >= max(0, limit):
            break

    result.boards = merged
    result.unresolved = unresolved
    return result
