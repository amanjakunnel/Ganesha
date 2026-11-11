"""Resolve referral companies to Greenhouse / Lever / Ashby board tokens.

Board tokens are derived from existing JobPosting apply/canonical URLs.
Unique company names come from the referral contacts sheet adapter.
Contact PII is never logged or attached to jobs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy.orm import Session, joinedload

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
    rows = (
        session.query(JobPosting)
        .options(joinedload(JobPosting.company))
        .order_by(JobPosting.scraped_at.desc().nullslast())
        .limit(limit_scan)
        .all()
    )
    out: list[BoardCandidate] = []
    seen: set[str] = set()
    for job in rows:
        company_name = ""
        if job.company is not None:
            company_name = job.company.canonical_name or ""
        for url in (job.company_apply_url, job.canonical_url):
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
    companies, sheet_error = load_pilot_companies(limit=limit, csv_text=csv_text)
    result = ResolutionResult(companies=companies, sheet_error=sheet_error)
    if sheet_error:
        return result
    if not companies:
        return result
    candidates = collect_board_candidates_from_jobs(session)
    boards, unresolved = resolve_boards_for_companies(companies, candidates)
    result.boards = boards
    result.unresolved = unresolved
    return result
