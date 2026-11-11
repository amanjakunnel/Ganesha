"""Ashby public job posting API adapter (GET only; no auth).

Endpoint: GET https://api.ashbyhq.com/posting-api/job-board/{JOB_BOARD_NAME}
Docs: https://developers.ashbyhq.com/docs/public-job-posting-api
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlparse

from packages.core.services.board_discovery.http_client import BoardHttpClient
from packages.core.services.board_discovery.types import (
    PLATFORM_ASHBY,
    BoardFetchResult,
    BoardJob,
    BoardRef,
)

_ASHBY_HOSTS = frozenset({"jobs.ashbyhq.com", "api.ashbyhq.com"})


def extract_ashby_board_name(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _ASHBY_HOSTS:
        return None
    path = parsed.path or ""
    api_match = re.match(
        r"^/posting-api/job-board/(?P<name>[A-Za-z0-9_.-]+)(?:/|$)",
        path,
        re.IGNORECASE,
    )
    if api_match:
        return api_match.group("name")
    match = re.match(r"^/(?P<name>[A-Za-z0-9_.-]+)(?:/|$)", path)
    if not match:
        return None
    name = match.group("name")
    if name.lower() in {"posting-api", "api", "apply"}:
        return None
    return name


def api_url(board_name: str) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{board_name.strip()}"


def is_valid_jobs_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return False
    for job in jobs[:5]:
        if not isinstance(job, dict):
            return False
        if "title" not in job:
            return False
        if "jobUrl" not in job and "applyUrl" not in job:
            return False
    return True


def _external_id(raw: dict[str, Any]) -> str:
    for key in ("id", "jobPostingId", "jobId"):
        val = raw.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    url = (raw.get("jobUrl") or raw.get("applyUrl") or "").strip()
    if url:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    title = (raw.get("title") or "").strip()
    loc = (raw.get("location") or "").strip()
    digest = hashlib.sha256(f"{title}|{loc}".encode("utf-8")).hexdigest()[:24]
    return digest


def parse_jobs(
    payload: dict[str, Any],
    *,
    board: BoardRef,
) -> list[BoardJob]:
    out: list[BoardJob] = []
    for raw in payload.get("jobs") or []:
        if not isinstance(raw, dict):
            continue
        title = (raw.get("title") or "").strip()
        if not title:
            continue
        # Skip unlisted deep-link-only postings when explicitly marked.
        if raw.get("isListed") is False:
            continue
        job_url = (raw.get("jobUrl") or "").strip()
        apply_url = (raw.get("applyUrl") or job_url).strip()
        if not job_url and not apply_url:
            continue
        if not job_url:
            job_url = apply_url
        location = (raw.get("location") or "").strip() or None
        description = (raw.get("descriptionPlain") or "").strip()
        if not description:
            description = (
                f"Company: {board.company}\nRole: {title}\n"
                f"Location: {location or 'unspecified'}\n"
                f"Source: ashby board {board.board_token}"
            )
        workplace = raw.get("workplaceType")
        employment = raw.get("employmentType")
        out.append(
            BoardJob(
                platform=PLATFORM_ASHBY,
                board_token=board.board_token,
                external_id=_external_id(raw),
                title=title,
                company=board.company,
                location=location,
                description_text=description,
                canonical_url=job_url,
                company_apply_url=apply_url or job_url,
                workplace_type=str(workplace) if workplace else None,
                employment_type=str(employment) if employment else None,
                posted_at=raw.get("publishedAt"),
                raw_payload={"jobUrl": job_url},
            )
        )
    return out


def fetch_board(
    client: BoardHttpClient,
    board: BoardRef,
    *,
    etag: str | None = None,
) -> BoardFetchResult:
    url = api_url(board.board_token)
    try:
        response = client.get(url, etag=etag)
    except Exception as exc:  # noqa: BLE001
        return BoardFetchResult(
            board=board,
            ok=False,
            error=f"{exc.__class__.__name__}: {exc}",
        )
    if response.status_code == 304:
        return BoardFetchResult(
            board=board,
            ok=True,
            status_code=304,
            not_modified=True,
            etag=etag or response.headers.get("ETag"),
        )
    if response.status_code != 200:
        return BoardFetchResult(
            board=board,
            ok=False,
            status_code=response.status_code,
            error=f"HTTP {response.status_code}",
        )
    try:
        payload = response.json()
    except Exception:
        return BoardFetchResult(
            board=board,
            ok=False,
            status_code=response.status_code,
            error="invalid JSON",
        )
    if not is_valid_jobs_payload(payload):
        return BoardFetchResult(
            board=board,
            ok=False,
            status_code=response.status_code,
            error="unexpected JSON shape",
        )
    jobs = parse_jobs(payload, board=board)
    return BoardFetchResult(
        board=board,
        ok=True,
        status_code=200,
        etag=response.headers.get("ETag"),
        jobs=jobs,
    )
