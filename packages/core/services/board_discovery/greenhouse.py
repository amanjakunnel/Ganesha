"""Greenhouse Job Board API adapter (GET only).

Endpoint: GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs
Docs: https://developers.greenhouse.io/job-board.html
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlparse

from packages.core.services.board_discovery.http_client import BoardHttpClient
from packages.core.services.board_discovery.types import (
    PLATFORM_GREENHOUSE,
    BoardFetchResult,
    BoardJob,
    BoardRef,
)

_GH_HOSTS = frozenset(
    {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards-api.greenhouse.io",
    }
)
_TOKEN_RE = re.compile(
    r"^/(?P<token>[A-Za-z0-9_-]+)(?:/|$)",
)


def extract_greenhouse_token(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _GH_HOSTS and not host.endswith(".greenhouse.io"):
        return None
    path = parsed.path or ""
    if "/embed/" in path:
        return None
    # boards-api.greenhouse.io/v1/boards/{token}/jobs
    api_match = re.match(
        r"^/v1/boards/(?P<token>[A-Za-z0-9_-]+)(?:/|$)", path, re.IGNORECASE
    )
    if api_match:
        return api_match.group("token").lower()
    match = _TOKEN_RE.match(path)
    if not match:
        return None
    token = match.group("token").lower()
    if token in {"embed", "v1", "jobs", "api"}:
        return None
    return token


def api_url(board_token: str, *, content: bool = True) -> str:
    token = board_token.strip()
    base = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    return f"{base}?content=true" if content else base


def is_valid_jobs_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return False
    for job in jobs[:5]:
        if not isinstance(job, dict):
            return False
        if "id" not in job or "title" not in job:
            return False
    return True


def parse_jobs(
    payload: dict[str, Any],
    *,
    board: BoardRef,
) -> list[BoardJob]:
    out: list[BoardJob] = []
    for raw in payload.get("jobs") or []:
        if not isinstance(raw, dict):
            continue
        job_id = raw.get("id")
        title = (raw.get("title") or "").strip()
        if job_id is None or not title:
            continue
        absolute = (raw.get("absolute_url") or "").strip()
        if not absolute:
            absolute = (
                f"https://boards.greenhouse.io/{board.board_token}/jobs/{job_id}"
            )
        location_obj = raw.get("location") or {}
        location = None
        if isinstance(location_obj, dict):
            location = (location_obj.get("name") or "").strip() or None
        content = raw.get("content") or ""
        if isinstance(content, str) and content:
            description = html.unescape(
                re.sub(r"<[^>]+>", " ", content)
            )
            description = re.sub(r"\s+", " ", description).strip()
        else:
            description = (
                f"Company: {board.company}\nRole: {title}\n"
                f"Location: {location or 'unspecified'}\n"
                f"Source: greenhouse board {board.board_token}"
            )
        requisition = raw.get("requisition_id")
        req_str = str(requisition).strip() if requisition is not None else None
        out.append(
            BoardJob(
                platform=PLATFORM_GREENHOUSE,
                board_token=board.board_token,
                external_id=str(job_id),
                title=title,
                company=board.company,
                location=location,
                description_text=description,
                canonical_url=absolute,
                company_apply_url=absolute,
                requisition_id=req_str or None,
                posted_at=raw.get("updated_at") or raw.get("first_published"),
                raw_payload={"id": job_id, "requisition_id": requisition},
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
    except Exception as exc:  # noqa: BLE001 — surface as board error
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
