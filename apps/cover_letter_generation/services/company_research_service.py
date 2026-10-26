"""Limited official-source research. Called only from cover-letter plan/generate."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx

from apps.cover_letter_generation.paths import COMPANY_EVIDENCE_UNAVAILABLE
from apps.cover_letter_generation.schemas import CompanyEvidence

DISALLOWED_HOST_MARKERS = (
    "linkedin.",
    "reddit.",
    "github.",
    "glassdoor.",
    "indeed.",
    "simplify",
    "ashbyhq.",
    "greenhouse.",
    "lever.co",
    "myworkdayjobs.",
    "workable.",
    "wikipedia.",
    "medium.com",
    "twitter.",
    "x.com",
    "facebook.",
)

_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.I | re.S)
_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")

Transport = Callable[[str], tuple[int, str]]


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def is_disallowed_source(url: str) -> bool:
    host = _host(url)
    return any(marker in host for marker in DISALLOWED_HOST_MARKERS)


def is_official_host(url: str, *, company_name: str, website_domain: str | None) -> bool:
    if is_disallowed_source(url):
        return False
    host = _host(url)
    if not host:
        return False
    domain = (website_domain or "").lower().strip().lstrip(".")
    if domain and (host == domain or host.endswith("." + domain)):
        return True
    token = re.sub(r"[^a-z0-9]", "", company_name.lower())
    if len(token) < 4:
        return False
    compact = host.replace(".", "")
    return token in compact or token[:8] in compact


def _source_type(url: str) -> str:
    path = urlparse(url).path.lower()
    if any(part in path for part in ("/engineering", "/eng-blog", "/tech-blog")):
        return "engineering_blog"
    if any(part in path for part in ("/news", "/newsroom", "/blog", "/press")):
        return "newsroom"
    if "/product" in path:
        return "product"
    if any(part in path for part in ("/docs", "/documentation")):
        return "docs"
    if any(part in path for part in ("/careers", "/jobs")):
        return "careers"
    if "github.com" in _host(url):
        return "opensource"
    return "about"


def _visible_text(html: str) -> tuple[str, str]:
    title_match = _TITLE_RE.search(html)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    text = _TAG_RE.sub(" ", html)
    text = re.sub(r"https?://\S+|www\.\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return title[:180], text[:500]


def _default_transport(url: str) -> tuple[int, str]:
    headers = {"User-Agent": "GaneshaCoverLetter/1.0"}
    with httpx.Client(timeout=8.0, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        final = str(response.url)
        if is_disallowed_source(final):
            raise ValueError("redirected to a disallowed host")
        return response.status_code, response.text


def _candidate_urls(company_name: str, website_domain: str | None, apply_url: str | None) -> list[str]:
    urls: list[str] = []
    domain = (website_domain or "").strip().lstrip(".")
    if domain and not is_disallowed_source(f"https://{domain}/about"):
        urls.append(f"https://{domain}/about")
        urls.append(f"https://{domain}/careers")
        return urls[:2]
    if apply_url and is_official_host(
        apply_url, company_name=company_name, website_domain=None
    ):
        host = _host(apply_url)
        urls.append(f"https://{host}/about")
    return urls[:2]


def research_company(
    *,
    company_name: str,
    website_domain: str | None = None,
    apply_url: str | None = None,
    transport: Transport | None = None,
    skip: bool = False,
    now: datetime | None = None,
) -> tuple[list[CompanyEvidence], list[str]]:
    """Fetch at most two official pages. Failures degrade to job-description-only."""
    if skip:
        return [], [COMPANY_EVIDENCE_UNAVAILABLE]
    urls = _candidate_urls(company_name, website_domain, apply_url)
    if not urls:
        return [], [COMPANY_EVIDENCE_UNAVAILABLE]

    fetch = transport or _default_transport
    clock = now or datetime.now(UTC)
    evidence: list[CompanyEvidence] = []
    warnings: list[str] = []
    for url in urls:
        if is_disallowed_source(url) or not is_official_host(
            url, company_name=company_name, website_domain=website_domain
        ):
            warnings.append(f"Rejected non-official source: {url}")
            continue
        try:
            status, body = fetch(url)
        except Exception:
            warnings.append(COMPANY_EVIDENCE_UNAVAILABLE)
            continue
        if status >= 400 or not body.strip():
            warnings.append(COMPANY_EVIDENCE_UNAVAILABLE)
            continue
        title, excerpt = _visible_text(body)
        if len(excerpt) < 40:
            continue
        source_type = _source_type(url)
        published = None
        usable = True
        date_match = _DATE_RE.search(body[:2000])
        if date_match:
            published = date_match.group(1)
        if source_type == "newsroom":
            if not published:
                usable = False
            else:
                published_dt = datetime.fromisoformat(published).replace(tzinfo=UTC)
                if clock - published_dt > timedelta(days=180):
                    usable = False
        evidence.append(
            CompanyEvidence(
                url=url,
                title=title or company_name,
                source_type=source_type,
                retrieved_at=clock.isoformat(),
                excerpt=excerpt,
                evidence_id=hashlib.sha256(url.encode()).hexdigest()[:16],
                usable=usable,
                published_at=published,
            )
        )
    kept = [item for item in evidence if item.usable]
    if not kept:
        warnings.append(COMPANY_EVIDENCE_UNAVAILABLE)
    return kept, warnings
