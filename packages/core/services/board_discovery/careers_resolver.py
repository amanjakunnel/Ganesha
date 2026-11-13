"""Prototype careers-page board resolver for referral companies.

Finds Greenhouse / Lever / Ashby board tokens for companies that lack a
board URL in existing JobPosting rows. Uses public GET only; never bypasses
CAPTCHA/login; never logs contact PII or performs outreach.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from packages.core.domain.models import Company, JobPosting
from packages.core.services.board_discovery import ashby, greenhouse, lever
from packages.core.services.board_discovery.http_client import BoardHttpClient
from packages.core.services.board_discovery.types import (
    PLATFORM_ASHBY,
    PLATFORM_GREENHOUSE,
    PLATFORM_LEVER,
    USER_AGENT,
    BoardRef,
    UnresolvedCompany,
)
from packages.core.services.company_normalize import (
    companies_likely_same,
    normalize_company_key,
)

BOARD_RESOLUTIONS_PATH = Path.home() / ".ganesha" / "board_resolutions.json"

# Honest UA for HTML careers pages (BoardHttpClient default is JSON-oriented).
_HTML_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"

_ATS_LINK_RE = re.compile(
    r"https?://(?:(?:job-)?boards(?:-api)?\.greenhouse\.io|"
    r"boards\.greenhouse\.io|"
    r"jobs(?:\.eu)?\.lever\.co|"
    r"api(?:\.eu)?\.lever\.co|"
    r"jobs\.ashbyhq\.com|"
    r"api\.ashbyhq\.com)"
    r"/[A-Za-z0-9_.\-/%?=&#]+",
    re.IGNORECASE,
)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

_SKIP_HOST_FRAGMENTS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "simplify.jobs",
    "google.com",
    "facebook.com",
    "twitter.com",
    "youtube.com",
)


@dataclass
class CareersResolveHit:
    board: BoardRef
    method: str  # slug_probe | website_html | cache
    verified: bool = True


@dataclass
class CareersResolveBatch:
    resolved: list[CareersResolveHit] = field(default_factory=list)
    unresolved: list[UnresolvedCompany] = field(default_factory=list)
    from_cache: int = 0
    probed: int = 0

    def vendor_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for hit in self.resolved:
            counts[hit.board.platform] = counts.get(hit.board.platform, 0) + 1
        return counts

    def unresolved_reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.unresolved:
            counts[item.reason] = counts.get(item.reason, 0) + 1
        return counts


def company_slug_variants(company: str) -> list[str]:
    """Generate bounded slug guesses for ATS public board URLs."""
    key = normalize_company_key(company)
    raw = re.sub(r"[^a-z0-9\s\-]", "", (company or "").lower())
    compact = re.sub(r"\s+", "", key)
    dashed = re.sub(r"\s+", "-", key)
    underscored = re.sub(r"\s+", "_", key)
    variants: list[str] = []
    for v in (compact, dashed, underscored, raw.replace(" ", ""), raw.replace(" ", "-")):
        v = (v or "").strip("-_")
        if v and v not in variants and 2 <= len(v) <= 64:
            variants.append(v)
    # Drop corporate suffixes glued into compact form already handled by normalize.
    for extra in (f"{compact}inc", f"{dashed}-inc", f"{compact}hq", f"{dashed}-hq"):
        if extra not in variants and 2 <= len(extra) <= 64:
            variants.append(extra)
    return variants[:8]


def _slug_matches_company(token: str, company: str) -> bool:
    tk = normalize_company_key(token.replace("-", " ").replace("_", " "))
    ck = normalize_company_key(company)
    if not tk or not ck:
        return False
    t_compact = re.sub(r"\s+", "", tk)
    c_compact = re.sub(r"\s+", "", ck)
    if t_compact == c_compact:
        return True
    # Do not use companies_likely_same here — it allows any substring and
    # creates duplicate/wrong board matches ("ai"⊂"openai", "data"⊂"databricks").
    # Near-equal compact containment only for longer tokens.
    if len(t_compact) >= 5 and len(c_compact) >= 5:
        shorter, longer = (
            (t_compact, c_compact)
            if len(t_compact) <= len(c_compact)
            else (c_compact, t_compact)
        )
        if shorter in longer and len(shorter) / len(longer) >= 0.7:
            return True
    return False


def _title_matches_company(html: str, company: str) -> bool:
    m = _TITLE_RE.search(html or "")
    if not m:
        return False
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    return companies_likely_same(title, company) or _slug_matches_company(
        re.sub(r"[^a-z0-9]+", " ", title.lower()), company
    )


def _payload_company_name(platform: str, payload: Any) -> str | None:
    if platform == PLATFORM_ASHBY and isinstance(payload, dict):
        for key in ("companyName", "company_name", "name"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        jobs = payload.get("jobs") or []
        if jobs and isinstance(jobs[0], dict):
            dept = jobs[0].get("department")
            if isinstance(dept, str) and dept.strip():
                return None  # department ≠ company
    if platform == PLATFORM_LEVER and isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            cats = first.get("categories") or {}
            if isinstance(cats, dict):
                team = cats.get("team")
                if isinstance(team, str) and team.strip():
                    return None
            # Lever hosted pages rarely expose company in JSON list.
    if platform == PLATFORM_GREENHOUSE and isinstance(payload, dict):
        # List endpoint has no company field; rely on slug / HTML title.
        pass
    return None


def verify_board_belongs_to_company(
    client: BoardHttpClient,
    *,
    platform: str,
    token: str,
    company: str,
    region: str | None = None,
    html_hint: str | None = None,
) -> BoardRef | None:
    """Accept board only when API is live AND slug ~ company or title/name matches."""
    board = BoardRef(
        platform=platform,
        board_token=token,
        company=company,
        region=region,
    )
    slug_ok = _slug_matches_company(token, company)
    title_ok = bool(html_hint and _title_matches_company(html_hint, company))

    fetchers = {
        PLATFORM_GREENHOUSE: greenhouse.fetch_board,
        PLATFORM_LEVER: lever.fetch_board,
        PLATFORM_ASHBY: ashby.fetch_board,
    }
    fetcher = fetchers.get(platform)
    if fetcher is None:
        return None
    result = fetcher(client, board)
    if not result.ok:
        return None

    company_field_ok = False
    # Prefer company_name from raw payload when adapters expose it via jobs.
    if result.jobs:
        # No reliable company field on BoardJob (uses board.company we passed).
        pass
    # Optional: Ashby top-level companyName via a lightweight re-check only when
    # slug/title are weak — skip extra GET when ownership already clear.
    if not (slug_ok or title_ok) and platform == PLATFORM_ASHBY:
        try:
            resp = client.get(ashby.api_url(token))
            if resp.status_code == 200:
                name = _payload_company_name(platform, resp.json())
                if name and (
                    companies_likely_same(name, company)
                    or _slug_matches_company(name, company)
                ):
                    company_field_ok = True
        except Exception:
            company_field_ok = False

    if slug_ok or title_ok or company_field_ok:
        return board
    return None


def _probe_ats_slugs(
    client: BoardHttpClient,
    company: str,
) -> CareersResolveHit | UnresolvedCompany | None:
    """Public GET of known ATS API/board URL patterns with slug variants.

    Hits board APIs directly. Ownership requires slug ~ company (or HTML title).
    """
    saw_rate_limit = False
    # Bound probes: compact + dashed variants only.
    slugs = company_slug_variants(company)[:3]
    for slug in slugs:
        for platform, token, region in (
            (PLATFORM_GREENHOUSE, slug, None),
            (PLATFORM_LEVER, slug, None),
            (PLATFORM_ASHBY, slug, None),
        ):
            verified = verify_board_belongs_to_company(
                client,
                platform=platform,
                token=token,
                company=company,
                region=region,
            )
            if verified is not None:
                return CareersResolveHit(
                    board=verified, method="slug_probe", verified=True
                )
    # Duck-style HTML board page GET for title hint (one slug, three hosts).
    if slugs:
        primary_slug = slugs[0]
        for platform, page_url in (
            (PLATFORM_GREENHOUSE, f"https://boards.greenhouse.io/{primary_slug}"),
            (PLATFORM_LEVER, f"https://jobs.lever.co/{primary_slug}"),
            (PLATFORM_ASHBY, f"https://jobs.ashbyhq.com/{primary_slug}"),
        ):
            try:
                page = client.get(page_url, accept=_HTML_ACCEPT)
            except Exception:
                continue
            if page.status_code == 429:
                saw_rate_limit = True
                continue
            if page.status_code in {401, 403} or page.status_code >= 400:
                continue
            verified = verify_board_belongs_to_company(
                client,
                platform=platform,
                token=primary_slug,
                company=company,
                html_hint=page.text or "",
            )
            if verified is not None:
                return CareersResolveHit(
                    board=verified, method="slug_probe", verified=True
                )
    if saw_rate_limit:
        return UnresolvedCompany(company=company, reason="rate_limited")
    return None


def _extract_ats_from_html(html: str) -> list[tuple[str, str, str | None]]:
    found: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()
    for match in _ATS_LINK_RE.findall(html or ""):
        parsed = greenhouse.extract_greenhouse_token(match)
        if parsed:
            key = f"greenhouse|{parsed}"
            if key not in seen:
                seen.add(key)
                found.append((PLATFORM_GREENHOUSE, parsed, None))
            continue
        lever_parsed = lever.extract_lever_site(match)
        if lever_parsed:
            site, region = lever_parsed
            key = f"lever|{region or 'global'}|{site}"
            if key not in seen:
                seen.add(key)
                found.append((PLATFORM_LEVER, site, region))
            continue
        ash = ashby.extract_ashby_board_name(match)
        if ash:
            key = f"ashby|{ash}"
            if key not in seen:
                seen.add(key)
                found.append((PLATFORM_ASHBY, ash, None))
    return found


def discover_company_website_urls(
    session: Session,
    company: str,
    *,
    limit: int = 8,
) -> list[str]:
    """Infer company website / careers seeds from existing job URLs (no PII)."""
    from packages.core.services.board_discovery.company_resolution import (
        _company_keys_match,
    )
    from packages.core.services.referral_careers_pilot import PILOT_CAREERS_URLS

    seeds: list[str] = []
    known = PILOT_CAREERS_URLS.get(normalize_company_key(company))
    if known:
        seeds.append(known)

    rows = (
        session.query(
            JobPosting.company_apply_url,
            JobPosting.canonical_url,
            Company.canonical_name,
            Company.website_domain,
        )
        .outerjoin(Company, Company.id == JobPosting.company_id)
        .order_by(JobPosting.scraped_at.desc().nullslast())
        .limit(2000)
        .all()
    )
    domains: list[str] = []
    for apply_url, canonical_url, cname, website_domain in rows:
        if cname and not _company_keys_match(company, cname):
            continue
        if website_domain:
            host = website_domain.lower().lstrip(".")
            if host and host not in domains:
                domains.append(host)
        for url in (apply_url, canonical_url):
            if not url:
                continue
            host = (urlparse(url).netloc or "").lower()
            if host.startswith("www."):
                host = host[4:]
            if not host or any(frag in host for frag in _SKIP_HOST_FRAGMENTS):
                continue
            if host not in domains:
                domains.append(host)
        if len(domains) >= limit:
            break

    for host in domains[:limit]:
        base = f"https://{host}"
        for path in ("/careers", "/jobs", "/careers/jobs", "/company/careers", ""):
            url = f"{base}{path}"
            if url not in seeds:
                seeds.append(url)
    return seeds[: max(limit * 3, 12)]


def _probe_website_html(
    client: BoardHttpClient,
    session: Session,
    company: str,
) -> CareersResolveHit | UnresolvedCompany | None:
    seeds = discover_company_website_urls(session, company)
    if not seeds:
        return None
    saw_rate_limit = False
    hits: list[BoardRef] = []
    for url in seeds:
        try:
            resp = client.get(url, accept=_HTML_ACCEPT)
        except Exception:
            continue
        if resp.status_code == 429:
            saw_rate_limit = True
            continue
        if resp.status_code in {401, 403}:
            continue
        if resp.status_code >= 400:
            continue
        html = resp.text or ""
        candidates = _extract_ats_from_html(html)
        for platform, token, region in candidates:
            verified = verify_board_belongs_to_company(
                client,
                platform=platform,
                token=token,
                company=company,
                region=region,
                html_hint=html,
            )
            if verified is not None:
                hits.append(verified)
        if hits:
            break
    if len(hits) > 1:
        keys = {b.key for b in hits}
        if len(keys) > 1 and len({b.platform for b in hits}) == 1:
            return UnresolvedCompany(company=company, reason="ambiguous")
    if hits:
        return CareersResolveHit(board=hits[0], method="website_html", verified=True)
    if saw_rate_limit:
        return UnresolvedCompany(company=company, reason="rate_limited")
    return None


def load_board_resolutions(
    path: Path | None = None,
) -> dict[str, Any]:
    target = path or BOARD_RESOLUTIONS_PATH
    if not target.is_file():
        return {"boards": [], "unresolved": []}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"boards": [], "unresolved": []}
    if not isinstance(data, dict):
        return {"boards": [], "unresolved": []}
    return {
        "boards": list(data.get("boards") or []),
        "unresolved": list(data.get("unresolved") or []),
    }


def save_board_resolutions(
    *,
    resolved: Iterable[CareersResolveHit],
    unresolved: Iterable[UnresolvedCompany],
    path: Path | None = None,
) -> Path:
    target = path or BOARD_RESOLUTIONS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = load_board_resolutions(target)
    by_company: dict[str, dict[str, Any]] = {}
    for row in existing.get("boards") or []:
        if isinstance(row, dict) and row.get("company"):
            by_company[normalize_company_key(str(row["company"]))] = row
    unresolved_by: dict[str, dict[str, Any]] = {}
    for row in existing.get("unresolved") or []:
        if isinstance(row, dict) and row.get("company"):
            unresolved_by[normalize_company_key(str(row["company"]))] = row

    now = datetime.now(UTC).isoformat()
    for hit in resolved:
        key = normalize_company_key(hit.board.company)
        by_company[key] = {
            "platform": hit.board.platform,
            "board_token": hit.board.board_token,
            "company": hit.board.company,
            "region": hit.board.region,
            "method": hit.method,
            "resolved_at": now,
        }
        unresolved_by.pop(key, None)
    for item in unresolved:
        key = normalize_company_key(item.company)
        if key in by_company:
            continue
        unresolved_by[key] = {
            "company": item.company,
            "reason": item.reason,
            "resolved_at": now,
        }

    payload = {
        "boards": list(by_company.values()),
        "unresolved": list(unresolved_by.values()),
        "updated_at": now,
        "user_agent": USER_AGENT,
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def cached_board_for_company(company: str, path: Path | None = None) -> BoardRef | None:
    data = load_board_resolutions(path)
    key = normalize_company_key(company)
    for row in data.get("boards") or []:
        if not isinstance(row, dict):
            continue
        if normalize_company_key(str(row.get("company") or "")) != key:
            continue
        platform = str(row.get("platform") or "")
        token = str(row.get("board_token") or "")
        if not platform or not token:
            continue
        region = row.get("region")
        return BoardRef(
            platform=platform,
            board_token=token,
            company=company,
            region=str(region) if region else None,
        )
    return None


def resolve_company_via_careers(
    session: Session,
    company: str,
    *,
    client: BoardHttpClient | None = None,
    use_cache: bool = True,
    persist: bool = False,
) -> CareersResolveHit | UnresolvedCompany:
    """Resolve one referral company to an ATS board token."""
    if use_cache:
        cached = cached_board_for_company(company)
        if cached is not None:
            return CareersResolveHit(board=cached, method="cache", verified=True)

    own = client is None
    if client is None:
        client = BoardHttpClient()
    try:
        slug_result = _probe_ats_slugs(client, company)
        if isinstance(slug_result, CareersResolveHit):
            if persist:
                save_board_resolutions(resolved=[slug_result], unresolved=[])
            return slug_result
        if isinstance(slug_result, UnresolvedCompany) and slug_result.reason in {
            "rate_limited",
            "ambiguous",
        }:
            if persist:
                save_board_resolutions(resolved=[], unresolved=[slug_result])
            return slug_result

        web_result = _probe_website_html(client, session, company)
        if isinstance(web_result, CareersResolveHit):
            if persist:
                save_board_resolutions(resolved=[web_result], unresolved=[])
            return web_result
        if isinstance(web_result, UnresolvedCompany):
            if persist:
                save_board_resolutions(resolved=[], unresolved=[web_result])
            return web_result

        # Prefer more specific reason from slug probe when present.
        if isinstance(slug_result, UnresolvedCompany):
            unresolved = slug_result
        else:
            unresolved = UnresolvedCompany(company=company, reason="no_careers_page")
        # If we probed ATS pages but ownership failed:
        if slug_result is None and web_result is None:
            # Distinguish verify failure when slug pages existed is hard without
            # extra state; keep no_careers_page for empty discovery.
            unresolved = UnresolvedCompany(company=company, reason="no_careers_page")
        if persist:
            save_board_resolutions(resolved=[], unresolved=[unresolved])
        return unresolved
    finally:
        if own:
            client.close()


def resolve_referral_companies_via_careers(
    session: Session,
    companies: Iterable[str],
    *,
    limit: int,
    client: BoardHttpClient | None = None,
    use_cache: bool = True,
    persist: bool = False,
) -> CareersResolveBatch:
    """Resolve up to ``limit`` referral companies via careers/ATS probes."""
    batch = CareersResolveBatch()
    own = client is None
    if client is None:
        client = BoardHttpClient(min_interval=0.35)
    try:
        for company in list(companies)[: max(0, limit)]:
            batch.probed += 1
            outcome = resolve_company_via_careers(
                session,
                company,
                client=client,
                use_cache=use_cache,
                persist=False,
            )
            if isinstance(outcome, CareersResolveHit):
                if outcome.method == "cache":
                    batch.from_cache += 1
                batch.resolved.append(outcome)
            else:
                batch.unresolved.append(outcome)
        if persist:
            save_board_resolutions(
                resolved=batch.resolved,
                unresolved=batch.unresolved,
            )
    finally:
        if own:
            client.close()
    return batch
