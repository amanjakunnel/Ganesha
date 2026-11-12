"""Lever Postings API adapter (GET only; never POST apply).

Endpoint: GET https://api.lever.co/v0/postings/{SITE}?mode=json
EU: https://api.eu.lever.co/v0/postings/{SITE}?mode=json
Docs: https://github.com/lever/postings-api
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from packages.core.services.board_discovery.http_client import BoardHttpClient
from packages.core.services.board_discovery.types import (
    PLATFORM_LEVER,
    BoardFetchResult,
    BoardJob,
    BoardRef,
)

_LEVER_HOSTS = {
    "jobs.lever.co": None,
    "jobs.eu.lever.co": "eu",
    "api.lever.co": None,
    "api.eu.lever.co": "eu",
}


def extract_lever_site(url: str | None) -> tuple[str, str | None] | None:
    """Return (site, region) where region is 'eu' or None for global."""
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _LEVER_HOSTS:
        return None
    region = _LEVER_HOSTS[host]
    path = parsed.path or ""
    # api.lever.co/v0/postings/{site}
    api_match = re.match(
        r"^/v0/postings/(?P<site>[A-Za-z0-9_-]+)(?:/|$)", path, re.IGNORECASE
    )
    if api_match:
        return api_match.group("site").lower(), region
    match = re.match(r"^/(?P<site>[A-Za-z0-9_-]+)(?:/|$)", path)
    if not match:
        return None
    site = match.group("site").lower()
    if site in {"v0", "postings"}:
        return None
    return site, region


def api_url(site: str, *, region: str | None = None) -> str:
    host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
    return f"https://{host}/v0/postings/{site.strip()}?mode=json"


def is_valid_jobs_payload(payload: Any) -> bool:
    # Lever list endpoint returns a JSON array of postings.
    if isinstance(payload, list):
        for job in payload[:5]:
            if not isinstance(job, dict):
                return False
            if "id" not in job or ("text" not in job and "title" not in job):
                return False
        return True
    # Some gateways wrap as {"data": [...]} — reject unknown wrappers.
    return False


def parse_jobs(
    payload: list[dict[str, Any]] | list[Any],
    *,
    board: BoardRef,
) -> list[BoardJob]:
    out: list[BoardJob] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        job_id = raw.get("id")
        title = (raw.get("text") or raw.get("title") or "").strip()
        if not job_id or not title:
            continue
        hosted = (raw.get("hostedUrl") or raw.get("applyUrl") or "").strip()
        if not hosted:
            host = (
                "jobs.eu.lever.co"
                if board.region == "eu"
                else "jobs.lever.co"
            )
            hosted = f"https://{host}/{board.board_token}/{job_id}"
        apply_url = (raw.get("applyUrl") or hosted).strip()
        categories = raw.get("categories") or {}
        location = None
        if isinstance(categories, dict):
            location = (categories.get("location") or "").strip() or None
            if not location:
                all_locs = categories.get("allLocations") or []
                if isinstance(all_locs, list) and all_locs:
                    location = str(all_locs[0])
        description = (
            raw.get("descriptionPlain")
            or raw.get("openingPlain")
            or raw.get("descriptionBodyPlain")
            or ""
        )
        if not str(description).strip():
            description = (
                f"Company: {board.company}\nRole: {title}\n"
                f"Location: {location or 'unspecified'}\n"
                f"Source: lever board {board.board_token}"
            )
        commitment = None
        if isinstance(categories, dict):
            commitment = (categories.get("commitment") or "").strip() or None
        out.append(
            BoardJob(
                platform=PLATFORM_LEVER,
                board_token=board.board_token,
                external_id=str(job_id),
                title=title,
                company=board.company,
                location=location,
                description_text=str(description).strip(),
                canonical_url=hosted,
                company_apply_url=apply_url,
                workplace_type=raw.get("workplaceType"),
                employment_type=commitment,
                raw_payload={"id": job_id},
            )
        )
    return out


def fetch_board(
    client: BoardHttpClient,
    board: BoardRef,
    *,
    etag: str | None = None,
) -> BoardFetchResult:
    url = api_url(board.board_token, region=board.region)
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
    assert isinstance(payload, list)
    jobs = parse_jobs(payload, board=board)
    return BoardFetchResult(
        board=board,
        ok=True,
        status_code=200,
        etag=response.headers.get("ETag"),
        jobs=jobs,
    )
