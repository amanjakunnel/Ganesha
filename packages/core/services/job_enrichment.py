"""Fetch full job descriptions for stub/aggregator listings.

Public GET only (Greenhouse / Lever / Ashby APIs, Workday CXS/HTML, generic pages).
No login, CAPTCHA bypass, or browser automation.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from sqlalchemy.orm import Session

from packages.core.domain.models import JobPosting, make_description_hash
from packages.core.services.job_eligibility import (
    attach_eligibility_to_metadata,
    is_stub_description,
    resolve_job_eligibility,
)
from packages.core.services.job_ranking import detect_intake_flags

USER_AGENT = "GaneshaJobEnrichment/0.1"
HTTP_TIMEOUT_SECONDS = 20.0
DEFAULT_MIN_REQUEST_INTERVAL = 0.75
ENRICHMENT_META_KEY = "enrichment"

_GH_HOSTS = frozenset(
    {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards-api.greenhouse.io",
    }
)
_LEVER_HOSTS = {
    "jobs.lever.co": None,
    "jobs.eu.lever.co": "eu",
    "api.lever.co": None,
    "api.eu.lever.co": "eu",
}
_ASHBY_HOSTS = frozenset({"jobs.ashbyhq.com", "api.ashbyhq.com"})
_WORKDAY_HOST = re.compile(r"^([\w-]+)\.wd\d+\.myworkdayjobs\.com$", re.I)


@dataclass
class FetchedJobContent:
    title: str | None
    description: str | None
    source_url: str
    provider: str
    etag: str | None = None
    raw_content_hash: str | None = None


@dataclass
class EnrichmentOutcome:
    job_id: str
    status: str  # enriched | unchanged | skipped | unavailable | failed
    provider: str | None = None
    source_url: str | None = None
    title_updated: bool = False
    previous_title: str | None = None
    description_updated: bool = False
    eligibility_result: str | None = None
    reason: str | None = None
    description_verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EnrichmentBatchResult:
    scanned: int = 0
    enriched: int = 0
    unchanged: int = 0
    skipped: int = 0
    unavailable: int = 0
    failed: int = 0
    title_drift: int = 0
    dry_run: bool = False
    outcomes: list[EnrichmentOutcome] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "enriched": self.enriched,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "unavailable": self.unavailable,
            "failed": self.failed,
            "title_drift": self.title_drift,
            "dry_run": self.dry_run,
            "outcomes": [o.to_dict() for o in self.outcomes[:50]],
        }


class EnrichmentHttpClient:
    """GET-only client with pacing and honest UA."""

    def __init__(
        self,
        *,
        min_interval: float = DEFAULT_MIN_REQUEST_INTERVAL,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._min_interval = min_interval
        self._last_request_at = 0.0
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "follow_redirects": True,
            "headers": {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            },
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)

    def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        accept: str | None = None,
    ) -> httpx.Response:
        self._pace()
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if accept:
            headers["Accept"] = accept
        response = self._client.get(url, headers=headers)
        self._last_request_at = time.monotonic()
        return response

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EnrichmentHttpClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _pace(self) -> None:
        if self._last_request_at <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self._min_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)


def _strip_html(text: str) -> str:
    unescaped = html.unescape(text or "")
    no_tags = re.sub(r"<[^>]+>", " ", unescaped)
    return re.sub(r"\s+", " ", no_tags).strip()


def _normalize_host(host: str) -> str:
    h = (host or "").lower()
    if h.startswith("www."):
        h = h[4:]
    return h


def job_needs_enrichment(job: JobPosting) -> bool:
    meta = job.intake_metadata or {}
    block = meta.get(ENRICHMENT_META_KEY) or {}
    if block.get("status") == "enriched" and not is_stub_description(
        job.description_text or ""
    ):
        return False
    if block.get("status") == "unavailable" and block.get("permanent"):
        return False
    return is_stub_description(job.description_text or "")


def description_verified(job: JobPosting | None, *, description: str | None = None) -> bool:
    if job is not None:
        meta = getattr(job, "intake_metadata", None) or {}
        block = meta.get(ENRICHMENT_META_KEY) or {}
        if block.get("status") == "enriched":
            return True
        text = description if description is not None else (job.description_text or "")
        return not is_stub_description(text)
    return not is_stub_description(description or "")


def _job_url(job: JobPosting) -> str | None:
    for candidate in (job.company_apply_url, job.canonical_url):
        if candidate and str(candidate).strip().startswith("http"):
            return str(candidate).strip()
    return None


def _clean_query(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


# --- Provider URL helpers -------------------------------------------------


def greenhouse_api_job_url(page_url: str) -> str | None:
    parsed = urlparse(page_url)
    host = _normalize_host(parsed.netloc)
    if host not in _GH_HOSTS and not host.endswith(".greenhouse.io"):
        return None
    path = parsed.path or ""
    api_match = re.match(
        r"^/v1/boards/(?P<token>[A-Za-z0-9_-]+)/jobs/(?P<jid>\d+)/?$",
        path,
        re.I,
    )
    if api_match:
        token, jid = api_match.group("token"), api_match.group("jid")
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}"
    match = re.match(
        r"^/(?P<token>[A-Za-z0-9_-]+)/jobs/(?P<jid>\d+)/?$",
        path,
        re.I,
    )
    if not match:
        return None
    token, jid = match.group("token"), match.group("jid")
    if token.lower() in {"embed", "v1", "jobs", "api"}:
        return None
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}"


def lever_api_job_url(page_url: str) -> str | None:
    parsed = urlparse(page_url)
    host = _normalize_host(parsed.netloc)
    if host not in _LEVER_HOSTS:
        return None
    region = _LEVER_HOSTS[host]
    path = parsed.path or ""
    api_match = re.match(
        r"^/v0/postings/(?P<site>[A-Za-z0-9_-]+)/(?P<jid>[A-Za-z0-9_-]+)/?$",
        path,
        re.I,
    )
    if api_match:
        site, jid = api_match.group("site"), api_match.group("jid")
    else:
        match = re.match(
            r"^/(?P<site>[A-Za-z0-9_-]+)/(?P<jid>[A-Za-z0-9_-]+)/?$",
            path,
        )
        if not match:
            return None
        site, jid = match.group("site"), match.group("jid")
        if site.lower() in {"v0", "postings"}:
            return None
    api_host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
    return f"https://{api_host}/v0/postings/{site}/{jid}?mode=json"


def ashby_board_and_job(page_url: str) -> tuple[str, str] | None:
    parsed = urlparse(page_url)
    host = _normalize_host(parsed.netloc)
    if host not in _ASHBY_HOSTS:
        return None
    path = parsed.path or ""
    match = re.match(
        r"^/(?P<board>[A-Za-z0-9_.-]+)(?:/.*)?/(?P<jid>[A-Za-z0-9_-]+)/?$",
        path,
    )
    if not match:
        match = re.match(r"^/(?P<board>[A-Za-z0-9_.-]+)/?$", path)
        if not match:
            return None
        return match.group("board"), ""
    board = match.group("board")
    if board.lower() in {"posting-api", "api", "apply"}:
        return None
    return board, match.group("jid")


def workday_cxs_url(page_url: str) -> str | None:
    """Derive Workday CXS job JSON URL from a careers page URL (plain GET, no login)."""
    parsed = urlparse(page_url.strip())
    host = _normalize_host(parsed.netloc)
    m = _WORKDAY_HOST.match(host)
    if not m:
        return None
    tenant_guess = m.group(1).lower()
    path = parsed.path or ""
    # /en-US/SITE/job/... or /SITE/job/...
    path = re.sub(r"^/en-[A-Za-z]{2}", "", path)
    job_match = re.match(
        r"^/(?P<site>[^/]+)/job/(?P<rest>.+)$",
        path,
    )
    if not job_match:
        # Sometimes location omitted: /SITE/job/Slug_REQ
        return None
    site = job_match.group("site")
    rest = job_match.group("rest").rstrip("/")
    return f"https://{host}/wday/cxs/{tenant_guess}/{site}/job/{rest}"


# --- Fetchers -------------------------------------------------------------


def _fetch_greenhouse(
    client: EnrichmentHttpClient, page_url: str, *, etag: str | None
) -> FetchedJobContent | None:
    api = greenhouse_api_job_url(page_url)
    if not api:
        return None
    response = client.get(api, etag=etag, accept="application/json")
    if response.status_code == 304:
        return FetchedJobContent(
            title=None,
            description=None,
            source_url=api,
            provider="greenhouse",
            etag=etag,
            raw_content_hash=None,
        )
    if response.status_code != 200:
        raise RuntimeError(f"greenhouse_http_{response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict) or "title" not in payload:
        raise RuntimeError("greenhouse_bad_payload")
    title = (payload.get("title") or "").strip() or None
    content = payload.get("content") or ""
    description = _strip_html(content) if content else None
    return FetchedJobContent(
        title=title,
        description=description,
        source_url=api,
        provider="greenhouse",
        etag=response.headers.get("ETag"),
        raw_content_hash=make_description_hash(description or title or ""),
    )


def _fetch_lever(
    client: EnrichmentHttpClient, page_url: str, *, etag: str | None
) -> FetchedJobContent | None:
    api = lever_api_job_url(page_url)
    if not api:
        return None
    response = client.get(api, etag=etag, accept="application/json")
    if response.status_code == 304:
        return FetchedJobContent(
            title=None,
            description=None,
            source_url=api,
            provider="lever",
            etag=etag,
        )
    if response.status_code != 200:
        raise RuntimeError(f"lever_http_{response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("lever_bad_payload")
    title = (payload.get("text") or payload.get("title") or "").strip() or None
    parts: list[str] = []
    for key in ("description", "descriptionPlain", "additional", "lists"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(_strip_html(val))
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    parts.append(_strip_html(str(item.get("text") or "")))
                    parts.append(_strip_html(str(item.get("content") or "")))
    description = re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip() or None
    return FetchedJobContent(
        title=title,
        description=description,
        source_url=api,
        provider="lever",
        etag=response.headers.get("ETag"),
        raw_content_hash=make_description_hash(description or title or ""),
    )


def _fetch_ashby(
    client: EnrichmentHttpClient, page_url: str, *, etag: str | None
) -> FetchedJobContent | None:
    parsed = ashby_board_and_job(page_url)
    if not parsed:
        return None
    board, job_hint = parsed
    api = f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
    response = client.get(api, etag=etag, accept="application/json")
    if response.status_code == 304:
        return FetchedJobContent(
            title=None,
            description=None,
            source_url=api,
            provider="ashby",
            etag=etag,
        )
    if response.status_code != 200:
        raise RuntimeError(f"ashby_http_{response.status_code}")
    payload = response.json()
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise RuntimeError("ashby_bad_payload")
    clean_page = _clean_query(page_url)
    chosen: dict[str, Any] | None = None
    for raw in jobs:
        if not isinstance(raw, dict):
            continue
        urls = [
            str(raw.get("jobUrl") or ""),
            str(raw.get("applyUrl") or ""),
        ]
        if any(_clean_query(u) == clean_page for u in urls if u):
            chosen = raw
            break
        if job_hint and job_hint in (str(raw.get("id") or ""), str(raw.get("jobPostingId") or "")):
            chosen = raw
            break
        if job_hint and any(job_hint in u for u in urls):
            chosen = raw
            break
    if chosen is None:
        raise RuntimeError("ashby_job_not_found")
    title = (chosen.get("title") or "").strip() or None
    description = (chosen.get("descriptionPlain") or "").strip() or None
    if not description and chosen.get("descriptionHtml"):
        description = _strip_html(str(chosen.get("descriptionHtml")))
    return FetchedJobContent(
        title=title,
        description=description,
        source_url=api,
        provider="ashby",
        etag=response.headers.get("ETag"),
        raw_content_hash=make_description_hash(description or title or ""),
    )


def _fetch_workday(
    client: EnrichmentHttpClient, page_url: str, *, etag: str | None
) -> FetchedJobContent | None:
    cxs = workday_cxs_url(page_url)
    if cxs:
        response = client.get(cxs, etag=etag, accept="application/json")
        if response.status_code == 304:
            return FetchedJobContent(
                title=None,
                description=None,
                source_url=cxs,
                provider="workday_cxs",
                etag=etag,
            )
        if response.status_code == 200:
            try:
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError("workday_bad_json") from exc
            info = payload.get("jobPostingInfo") if isinstance(payload, dict) else None
            if isinstance(info, dict):
                title = (info.get("title") or "").strip() or None
                description = _strip_html(str(info.get("jobDescription") or "")) or None
                if title or description:
                    return FetchedJobContent(
                        title=title,
                        description=description,
                        source_url=cxs,
                        provider="workday_cxs",
                        etag=response.headers.get("ETag"),
                        raw_content_hash=make_description_hash(
                            description or title or ""
                        ),
                    )

    # Fallback: HTML meta / embedded JSON (title + short og:description).
    response = client.get(page_url, accept="text/html,application/xhtml+xml")
    if response.status_code != 200:
        raise RuntimeError(f"workday_html_{response.status_code}")
    body = response.text or ""
    title = None
    og_title = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        body,
        re.I,
    )
    if not og_title:
        og_title = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
            body,
            re.I,
        )
    if og_title:
        title = html.unescape(og_title.group(1)).strip() or None
    description = None
    og_desc = re.search(
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
        body,
        re.I,
    )
    if not og_desc:
        og_desc = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
            body,
            re.I,
        )
    if og_desc:
        description = html.unescape(og_desc.group(1)).strip() or None
    # Embedded JSON title (Workday often ships title in page JSON without full JD).
    if not title:
        embedded = re.search(
            r'"title"\s*:\s*"([^"\\]{8,300})"',
            body,
        )
        if embedded:
            title = html.unescape(embedded.group(1)).strip() or None
    if not title and not description:
        # JS SPA shell with no useful content → unavailable (no browser automation).
        raise RuntimeError("workday_js_shell")
    return FetchedJobContent(
        title=title,
        description=description,
        source_url=_clean_query(page_url),
        provider="workday_html",
        etag=response.headers.get("ETag"),
        raw_content_hash=make_description_hash(description or title or ""),
    )


def _fetch_generic(
    client: EnrichmentHttpClient, page_url: str, *, etag: str | None
) -> FetchedJobContent:
    response = client.get(page_url, etag=etag)
    if response.status_code == 304:
        return FetchedJobContent(
            title=None,
            description=None,
            source_url=page_url,
            provider="generic",
            etag=etag,
        )
    if response.status_code != 200:
        raise RuntimeError(f"generic_http_{response.status_code}")
    ctype = (response.headers.get("content-type") or "").lower()
    body = response.text or ""
    if "login" in body.lower()[:2000] and "password" in body.lower()[:4000]:
        raise RuntimeError("login_required")
    if "captcha" in body.lower()[:4000]:
        raise RuntimeError("captcha_required")

    title = None
    description = None
    if "application/json" in ctype:
        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("generic_bad_json") from exc
        if isinstance(payload, dict):
            if payload.get("widget") == "redirect":
                raise RuntimeError("js_redirect_shell")
            info = payload.get("jobPostingInfo")
            if isinstance(info, dict):
                title = (info.get("title") or "").strip() or None
                description = _strip_html(str(info.get("jobDescription") or "")) or None
            title = title or (str(payload.get("title") or "").strip() or None)
            for key in ("description", "descriptionPlain", "jobDescription", "content"):
                if payload.get(key):
                    description = _strip_html(str(payload.get(key)))
                    break
    else:
        og_title = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            body,
            re.I,
        )
        if og_title:
            title = html.unescape(og_title.group(1)).strip() or None
        og_desc = re.search(
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
            body,
            re.I,
        )
        if og_desc:
            description = html.unescape(og_desc.group(1)).strip() or None
        # Reject thin SPA shells without extractable content.
        if not title and not description:
            if len(body) < 2000 or "id=\"root\"" in body or "id='root'" in body:
                raise RuntimeError("js_shell_no_content")

    if not title and not description:
        raise RuntimeError("no_extractable_content")
    return FetchedJobContent(
        title=title,
        description=description,
        source_url=_clean_query(page_url),
        provider="generic",
        etag=response.headers.get("ETag"),
        raw_content_hash=make_description_hash(description or title or ""),
    )


def fetch_job_content(
    client: EnrichmentHttpClient,
    page_url: str,
    *,
    etag: str | None = None,
) -> FetchedJobContent:
    host = _normalize_host(urlparse(page_url).netloc)
    errors: list[str] = []

    try:
        if host in _GH_HOSTS or host.endswith(".greenhouse.io"):
            result = _fetch_greenhouse(client, page_url, etag=etag)
            if result:
                return result
    except Exception as exc:  # noqa: BLE001
        errors.append(f"greenhouse:{exc}")

    try:
        if host in _LEVER_HOSTS:
            result = _fetch_lever(client, page_url, etag=etag)
            if result:
                return result
    except Exception as exc:  # noqa: BLE001
        errors.append(f"lever:{exc}")

    try:
        if host in _ASHBY_HOSTS:
            result = _fetch_ashby(client, page_url, etag=etag)
            if result:
                return result
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ashby:{exc}")

    try:
        if _WORKDAY_HOST.match(host):
            result = _fetch_workday(client, page_url, etag=etag)
            if result:
                return result
    except Exception as exc:  # noqa: BLE001
        errors.append(f"workday:{exc}")
        # Workday JS shells are a common unavailable case.
        if "js_shell" in str(exc) or "workday_js" in str(exc):
            raise RuntimeError("enrichment_unavailable_js") from exc

    try:
        return _fetch_generic(client, page_url, etag=etag)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"generic:{exc}")
        msg = str(exc)
        if any(
            x in msg
            for x in (
                "login_required",
                "captcha_required",
                "js_shell",
                "js_redirect",
                "no_extractable",
            )
        ):
            raise RuntimeError("enrichment_unavailable") from exc
        raise RuntimeError(";".join(errors) or "fetch_failed") from exc


def _titles_differ(stored: str, live: str | None) -> bool:
    if not live:
        return False
    a = re.sub(r"\s+", " ", stored or "").strip().lower()
    b = re.sub(r"\s+", " ", live).strip().lower()
    return bool(a and b and a != b)


def _apply_enrichment(
    job: JobPosting,
    fetched: FetchedJobContent,
    *,
    dry_run: bool,
) -> EnrichmentOutcome:
    meta = dict(job.intake_metadata or {})
    prior = dict(meta.get(ENRICHMENT_META_KEY) or {})
    prior_hash = prior.get("content_hash")
    new_hash = fetched.raw_content_hash or make_description_hash(
        fetched.description or fetched.title or ""
    )

    # Conditional skip: unchanged remote content.
    if (
        prior_hash
        and prior_hash == new_hash
        and prior.get("status") == "enriched"
        and not is_stub_description(job.description_text or "")
    ):
        return EnrichmentOutcome(
            job_id=job.id,
            status="unchanged",
            provider=fetched.provider,
            source_url=fetched.source_url,
            description_verified=True,
            reason="content_hash_unchanged",
        )

    # 304 / empty body with existing etag
    if fetched.title is None and fetched.description is None and prior_hash:
        return EnrichmentOutcome(
            job_id=job.id,
            status="unchanged",
            provider=fetched.provider,
            source_url=fetched.source_url,
            description_verified=not is_stub_description(job.description_text or ""),
            reason="not_modified",
        )

    title_updated = False
    previous_title = None
    description_updated = False
    new_title = job.title
    new_description = job.description_text or ""

    if fetched.description and not is_stub_description(fetched.description):
        if fetched.description.strip() != (job.description_text or "").strip():
            description_updated = True
            new_description = fetched.description.strip()
    elif fetched.description and is_stub_description(job.description_text or ""):
        # Even short og:description is better than aggregator stub for eligibility cues.
        if len(fetched.description.strip()) > len((job.description_text or "").strip()):
            description_updated = True
            new_description = fetched.description.strip()

    if _titles_differ(job.title, fetched.title):
        title_updated = True
        previous_title = job.title
        new_title = (fetched.title or job.title).strip()

    if not description_updated and not title_updated and prior.get("status") == "enriched":
        return EnrichmentOutcome(
            job_id=job.id,
            status="unchanged",
            provider=fetched.provider,
            source_url=fetched.source_url,
            description_verified=True,
            reason="no_field_changes",
        )

    if dry_run:
        from packages.core.services.job_eligibility import classify_job_eligibility

        eligibility = classify_job_eligibility(
            title=new_title,
            description=new_description,
            company_apply_url=job.company_apply_url,
            location=job.location,
            employment_type=job.employment_type,
            intake_flags=detect_intake_flags(new_title, new_description),
            source_markers=(meta.get("simplify_markers") or {}),
            description_verified=not is_stub_description(new_description),
        )
        return EnrichmentOutcome(
            job_id=job.id,
            status="enriched" if description_updated or title_updated else "unchanged",
            provider=fetched.provider,
            source_url=fetched.source_url,
            title_updated=title_updated,
            previous_title=previous_title,
            description_updated=description_updated,
            eligibility_result=eligibility.result,
            description_verified=not is_stub_description(new_description),
            reason="dry_run",
        )

    if description_updated:
        job.description_text = new_description
        job.description_hash = make_description_hash(new_description)
    if title_updated and fetched.title:
        job.title = new_title
        job.normalized_title = new_title.lower().strip()

    enrichment_block: dict[str, Any] = {
        "status": "enriched",
        "provider": fetched.provider,
        "source_url": fetched.source_url,
        "fetched_at": datetime.now(UTC).isoformat(),
        "content_hash": new_hash,
        "etag": fetched.etag,
        "description_verified": not is_stub_description(job.description_text or ""),
    }
    if previous_title:
        enrichment_block["previous_title"] = previous_title
        enrichment_block["title_drift"] = True
    meta[ENRICHMENT_META_KEY] = enrichment_block
    meta["flags"] = detect_intake_flags(job.title, job.description_text or "")
    job.intake_metadata = meta
    eligibility = resolve_job_eligibility(job)
    job.intake_metadata = attach_eligibility_to_metadata(meta, eligibility)
    return EnrichmentOutcome(
        job_id=job.id,
        status="enriched",
        provider=fetched.provider,
        source_url=fetched.source_url,
        title_updated=title_updated,
        previous_title=previous_title,
        description_updated=description_updated,
        eligibility_result=eligibility.result,
        description_verified=True,
    )


def _mark_failed(
    job: JobPosting,
    *,
    status: str,
    reason: str,
    source_url: str | None,
    dry_run: bool,
) -> EnrichmentOutcome:
    verified = description_verified(job)
    if dry_run:
        return EnrichmentOutcome(
            job_id=job.id,
            status=status,
            source_url=source_url,
            reason=reason,
            description_verified=verified,
        )
    meta = dict(job.intake_metadata or {})
    block = dict(meta.get(ENRICHMENT_META_KEY) or {})
    block.update(
        {
            "status": status,
            "reason": reason,
            "fetched_at": datetime.now(UTC).isoformat(),
            "source_url": source_url,
            "description_verified": verified,
            "permanent": status == "unavailable",
        }
    )
    meta[ENRICHMENT_META_KEY] = block
    job.intake_metadata = meta
    eligibility = resolve_job_eligibility(job)
    job.intake_metadata = attach_eligibility_to_metadata(meta, eligibility)
    return EnrichmentOutcome(
        job_id=job.id,
        status=status,
        source_url=source_url,
        reason=reason,
        eligibility_result=eligibility.result,
        description_verified=verified,
    )


def enrich_job(
    session: Session,
    job: JobPosting,
    *,
    client: EnrichmentHttpClient | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> EnrichmentOutcome:
    """Enrich a single job posting in place (unless dry_run)."""
    if not force and not job_needs_enrichment(job):
        return EnrichmentOutcome(
            job_id=job.id,
            status="skipped",
            reason="not_stub_or_already_enriched",
            description_verified=description_verified(job),
        )

    url = _job_url(job)
    if not url:
        outcome = _mark_failed(
            job,
            status="unavailable",
            reason="missing_url",
            source_url=None,
            dry_run=dry_run,
        )
        if not dry_run:
            session.add(job)
        return outcome

    owns_client = client is None
    http = client or EnrichmentHttpClient()
    prior_etag = ((job.intake_metadata or {}).get(ENRICHMENT_META_KEY) or {}).get(
        "etag"
    )
    try:
        fetched = fetch_job_content(http, url, etag=prior_etag)
        outcome = _apply_enrichment(job, fetched, dry_run=dry_run)
        if not dry_run and outcome.status in {"enriched", "unavailable", "failed"}:
            session.add(job)
        return outcome
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        status = (
            "unavailable"
            if "unavailable" in msg or "js_shell" in msg or "login" in msg
            else "failed"
        )
        outcome = _mark_failed(
            job,
            status=status,
            reason=msg[:240],
            source_url=_clean_query(url),
            dry_run=dry_run,
        )
        if not dry_run:
            session.add(job)
        return outcome
    finally:
        if owns_client:
            http.close()


def _priority_candidates(
    session: Session,
    *,
    limit: int,
    only_recent_days: int | None = 7,
    include_feed_candidates: bool = True,
    eligible_fit_bands: set[str] | None = None,
    skip_excluded: bool = True,
) -> list[JobPosting]:
    """Prefer default-feed-ish recent stubs, then other stubs.

    When ``eligible_fit_bands`` is set (e.g. recommended,stretch), only stubs
    whose stored evidence_fit.band is in that set are returned. Excluded
    eligibility is skipped by default.
    """
    from sqlalchemy import or_

    from packages.core.services.evidence_fit_service import (
        BAND_RECOMMENDED,
        BAND_STRETCH,
        get_stored_fit,
    )
    from packages.core.services.job_eligibility import (
        ELIGIBILITY_ACTIONABLE,
        ELIGIBILITY_EXCLUDED,
        ELIGIBILITY_NEEDS_REVIEW,
        eligibility_from_metadata,
    )

    # Prefer likely aggregator stubs (short text / Simplify source marker).
    q = session.query(JobPosting).filter(
        or_(
            JobPosting.description_text.ilike("%SimplifyJobs%"),
            JobPosting.description_text.ilike("Company:%"),
        )
    )
    if only_recent_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=only_recent_days)
        q = q.filter(
            or_(
                JobPosting.scraped_at >= cutoff,
                JobPosting.discovered_at >= cutoff,
                JobPosting.created_at >= cutoff,
            )
        )
    q = q.order_by(JobPosting.scraped_at.desc().nullslast(), JobPosting.created_at.desc())
    # Oversample then filter with stub heuristics / already-enriched skip.
    rows = q.limit(max(limit * 40, 400)).all()
    stubs = [j for j in rows if job_needs_enrichment(j)]

    if skip_excluded or eligible_fit_bands:
        filtered: list[JobPosting] = []
        for job in stubs:
            elig = eligibility_from_metadata(job.intake_metadata)
            if skip_excluded and elig.result == ELIGIBILITY_EXCLUDED:
                continue
            if eligible_fit_bands is not None:
                fit = get_stored_fit(job) or {}
                band = str(fit.get("band") or "")
                if band not in eligible_fit_bands:
                    continue
            filtered.append(job)
        stubs = filtered

    def _score(job: JobPosting) -> tuple[int, float]:
        score = 0
        elig = eligibility_from_metadata(job.intake_metadata)
        fit = get_stored_fit(job) or {}
        band = fit.get("band")
        if include_feed_candidates and elig.result in {
            ELIGIBILITY_ACTIONABLE,
            ELIGIBILITY_NEEDS_REVIEW,
        }:
            score += 20
        if band in {BAND_RECOMMENDED, BAND_STRETCH}:
            score += 30
        if (job.status or "") in {"approved", "queued_for_review"}:
            score += 15
        # Shorter stubs first — more likely to change eligibility.
        score += max(0, 20 - len(job.description_text or "") // 30)
        ref = job.scraped_at or job.discovered_at or job.created_at
        age = 0.0
        if ref is not None:
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=UTC)
            age = -ref.timestamp()
        return (score, age)

    stubs.sort(key=_score, reverse=True)
    return stubs[:limit]


def enrich_jobs_batch(
    session: Session,
    *,
    limit: int = 25,
    dry_run: bool = False,
    only_recent_days: int | None = 7,
    job_ids: list[str] | None = None,
    force: bool = False,
    eligible_fit_bands: set[str] | None = None,
    skip_excluded: bool = True,
) -> EnrichmentBatchResult:
    result = EnrichmentBatchResult(dry_run=dry_run)
    if job_ids:
        jobs = (
            session.query(JobPosting)
            .filter(JobPosting.id.in_(job_ids))
            .all()
        )
    else:
        jobs = _priority_candidates(
            session,
            limit=limit,
            only_recent_days=only_recent_days,
            eligible_fit_bands=eligible_fit_bands,
            skip_excluded=skip_excluded,
        )
    result.scanned = len(jobs)
    with EnrichmentHttpClient() as client:
        for job in jobs:
            outcome = enrich_job(
                session, job, client=client, dry_run=dry_run, force=force
            )
            result.outcomes.append(outcome)
            if outcome.status == "enriched":
                result.enriched += 1
            elif outcome.status == "unchanged":
                result.unchanged += 1
            elif outcome.status == "skipped":
                result.skipped += 1
            elif outcome.status == "unavailable":
                result.unavailable += 1
            elif outcome.status == "failed":
                result.failed += 1
            if outcome.title_updated:
                result.title_drift += 1
    if not dry_run:
        session.flush()
    return result
