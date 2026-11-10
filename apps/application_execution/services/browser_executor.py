"""Browser execution interface — local Playwright now, remote executor later."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class FillFieldResult:
    field_key: str
    label: str
    status: str  # filled | skipped_existing | unsupported | sensitive | missing_value
    detail: str | None = None


@dataclass
class AutofillResult:
    ok: bool
    platform: str
    application_url: str
    filled: list[FillFieldResult] = field(default_factory=list)
    unresolved: list[dict[str, str]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    resume_uploaded: bool = False
    cover_uploaded: bool = False
    paused_for_review: bool = False
    safe_error: str | None = None
    submitted: bool = False  # always False in this milestone


@dataclass
class AutofillRequest:
    application_url: str
    resume_path: Path
    cover_letter_path: Path | None
    include_cover_letter: bool
    profile: object  # ApprovedProfile
    headless: bool = False
    keep_open: bool = True


class BrowserExecutor(Protocol):
    def fill_application(self, request: AutofillRequest) -> AutofillResult: ...
