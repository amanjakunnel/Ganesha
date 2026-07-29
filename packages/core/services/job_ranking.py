from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from packages.core.services.company_normalize import is_target_company
from packages.core.services.job_scoring import TRACK_KEYWORDS

CLEARANCE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"security clearance",
        r"active (ts|top secret|secret)",
        r"u\.?s\.? citizenship",
        r"must be a u\.?s\.? citizen",
        r"clearance required",
    )
]

SENIOR_TITLE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(senior|sr\.?|staff|principal|lead|director|vp|head of)\b",
        r"\b(10\+|15\+|20\+)\s*years",
    )
]

EARLY_CAREER_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"new college grad",
        r"new grad",
        r"entry[- ]level",
        r"early[- ]career",
        r"university grad",
        r"\b0[- ]?2 years\b",
        r"intern(ship)?\b",
    )
]

SOURCE_PRIORITY = {
    "symplicity": 25,
    "linkedin": 12,
    "manual": 15,
    "referral_sheet": 5,
    "company_page": 18,
    "simplifyjobs": 14,
}


@dataclass
class RankedJob:
    job_id: str
    score: int
    reasons: list[str] = field(default_factory=list)
    flags: dict[str, Any] = field(default_factory=dict)


def _track_fit_score(title: str, description: str) -> tuple[int, str | None]:
    text = f"{title}\n{description}".lower()
    best_track = None
    best = 0
    for track, kws in TRACK_KEYWORDS.items():
        hits = sum(1 for k in kws if k in text)
        score = hits * 8
        if score > best:
            best = score
            best_track = track
    return min(40, best), best_track


def _freshness_score(posted_at: datetime | None, scraped_at: datetime | None) -> tuple[int, str | None]:
    ref = scraped_at or posted_at
    if not ref:
        return 5, None
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)
    age_days = (datetime.now(UTC) - ref).days
    if age_days <= 7:
        return 20, f"fresh_{age_days}d"
    if age_days <= 30:
        return 12, f"recent_{age_days}d"
    if age_days <= 90:
        return 6, f"aging_{age_days}d"
    return 0, f"stale_{age_days}d"


def detect_intake_flags(title: str, description: str) -> dict[str, bool]:
    blob = f"{title}\n{description}"
    return {
        "clearance_concern": any(p.search(blob) for p in CLEARANCE_PATTERNS),
        "seniority_concern": any(p.search(title) for p in SENIOR_TITLE_PATTERNS),
        "early_career_signal": any(p.search(blob) for p in EARLY_CAREER_PATTERNS),
    }


def rank_job(
    *,
    job_id: str,
    title: str,
    company_name: str | None,
    description: str,
    source_name: str | None,
    posted_at: datetime | None,
    scraped_at: datetime | None,
    has_referral_contacts: bool,
    referral_contact_count: int,
    status: str,
    intake_metadata: dict[str, Any] | None,
) -> RankedJob:
    reasons: list[str] = []
    score = 0
    flags = detect_intake_flags(title, description)
    if intake_metadata:
        flags.update({k: v for k, v in intake_metadata.items() if k.endswith("_concern") or k.endswith("_signal")})

    if status in ("skipped", "deferred", "archived", "closed"):
        return RankedJob(job_id=job_id, score=-100, reasons=["inactive_status"], flags=flags)

    src = (source_name or "unknown").lower()
    src_pts = SOURCE_PRIORITY.get(src, 8)
    score += src_pts
    reasons.append(f"source_{src}(+{src_pts})")

    fresh_pts, fresh_reason = _freshness_score(posted_at, scraped_at)
    score += fresh_pts
    if fresh_reason:
        reasons.append(f"freshness_{fresh_reason}(+{fresh_pts})")

    track_pts, track = _track_fit_score(title, description)
    score += track_pts
    if track:
        reasons.append(f"track_fit_{track}(+{track_pts})")

    if flags.get("early_career_signal"):
        score += 18
        reasons.append("early_career(+18)")
    if flags.get("seniority_concern"):
        score -= 25
        reasons.append("senior_role(-25)")
    if flags.get("clearance_concern"):
        score -= 30
        reasons.append("clearance(-30)")

    is_target, target_name = is_target_company(company_name)
    if is_target:
        score += 15
        reasons.append(f"target_company_{target_name}(+15)")

    if has_referral_contacts:
        bonus = min(20, 8 + referral_contact_count * 2)
        score += bonus
        reasons.append(f"referral_network(+{bonus})")

    if len(description or "") < 120:
        score -= 8
        reasons.append("thin_description(-8)")

    return RankedJob(job_id=job_id, score=score, reasons=reasons, flags=flags)
