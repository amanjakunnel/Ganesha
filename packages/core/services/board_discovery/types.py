"""Shared types for company board discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


PLATFORM_GREENHOUSE = "greenhouse"
PLATFORM_LEVER = "lever"
PLATFORM_ASHBY = "ashby"

USER_AGENT = "GaneshaBoardDiscovery/0.1"
HTTP_TIMEOUT_SECONDS = 20.0
DEFAULT_MIN_REQUEST_INTERVAL = 1.0


@dataclass(frozen=True)
class BoardRef:
    platform: str
    board_token: str
    company: str
    region: str | None = None  # lever: "eu" | None (global)

    @property
    def key(self) -> str:
        region = self.region or "global"
        return f"{self.platform}|{region}|{self.board_token}"


@dataclass
class BoardJob:
    """Normalized job row from a public board API."""

    platform: str
    board_token: str
    external_id: str
    title: str
    company: str
    location: str | None
    description_text: str
    canonical_url: str
    company_apply_url: str
    requisition_id: str | None = None
    workplace_type: str | None = None
    employment_type: str | None = None
    posted_at: str | None = None
    raw_payload: dict[str, Any] | None = None

    @property
    def source_import_key(self) -> str:
        return f"{self.platform}|{self.board_token}|{self.external_id}"


@dataclass
class UnresolvedCompany:
    company: str
    reason: str


@dataclass
class BoardFetchResult:
    board: BoardRef
    ok: bool
    status_code: int | None = None
    not_modified: bool = False
    etag: str | None = None
    jobs: list[BoardJob] = field(default_factory=list)
    error: str | None = None
