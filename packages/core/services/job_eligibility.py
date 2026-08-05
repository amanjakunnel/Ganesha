from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from packages.core.services.job_ranking import detect_intake_flags

ELIGIBILITY_ACTIONABLE = "actionable"
ELIGIBILITY_NEEDS_REVIEW = "needs_review"
ELIGIBILITY_EXCLUDED = "excluded"


class EligibilityReason(str, Enum):
    CLEARANCE_REQUIREMENT = "clearance_requirement"
    CITIZENSHIP_REQUIREMENT = "citizenship_requirement"
    SPONSORSHIP_UNAVAILABLE = "sponsorship_unavailable"
    LISTING_CLOSED = "listing_closed"
    SENIORITY_MISMATCH = "seniority_mismatch"
    MISSING_APPLY_URL = "missing_apply_url"
    THIN_DESCRIPTION = "thin_description"
    INCOMPLETE_SOURCE_ROW = "incomplete_source_row"


REASON_LABELS: dict[str, str] = {
    EligibilityReason.CLEARANCE_REQUIREMENT.value: "clearance requirement",
    EligibilityReason.CITIZENSHIP_REQUIREMENT.value: "citizenship requirement",
    EligibilityReason.SPONSORSHIP_UNAVAILABLE.value: "sponsorship unavailable",
    EligibilityReason.LISTING_CLOSED.value: "listing closed",
    EligibilityReason.SENIORITY_MISMATCH.value: "seniority mismatch",
    EligibilityReason.MISSING_APPLY_URL.value: "missing apply URL",
    EligibilityReason.THIN_DESCRIPTION.value: "thin description",
    EligibilityReason.INCOMPLETE_SOURCE_ROW.value: "incomplete source row",
}


@dataclass(frozen=True)
class EligibilityResult:
    result: str
    reason_category: str | None

    def is_actionable(self) -> bool:
        return self.result == ELIGIBILITY_ACTIONABLE


def classify_job_eligibility(
    *,
    title: str,
    description: str,
    company_apply_url: str | None = None,
    intake_flags: dict[str, bool] | None = None,
    source_markers: dict[str, bool] | None = None,
    incomplete_row: bool = False,
) -> EligibilityResult:
    """Deterministic eligibility classification without echoing sensitive posting text."""
    flags = intake_flags or detect_intake_flags(title, description)
    markers = source_markers or {}

    if incomplete_row:
        return EligibilityResult(
            ELIGIBILITY_NEEDS_REVIEW, EligibilityReason.INCOMPLETE_SOURCE_ROW.value
        )

    if markers.get("listing_closed"):
        return EligibilityResult(
            ELIGIBILITY_EXCLUDED, EligibilityReason.LISTING_CLOSED.value
        )

    if flags.get("clearance_concern"):
        return EligibilityResult(
            ELIGIBILITY_EXCLUDED, EligibilityReason.CLEARANCE_REQUIREMENT.value
        )

    if markers.get("citizenship_required"):
        return EligibilityResult(
            ELIGIBILITY_EXCLUDED, EligibilityReason.CITIZENSHIP_REQUIREMENT.value
        )

    if markers.get("no_sponsorship"):
        return EligibilityResult(
            ELIGIBILITY_EXCLUDED, EligibilityReason.SPONSORSHIP_UNAVAILABLE.value
        )

    if flags.get("seniority_concern"):
        return EligibilityResult(
            ELIGIBILITY_NEEDS_REVIEW, EligibilityReason.SENIORITY_MISMATCH.value
        )

    if not (company_apply_url or "").strip():
        return EligibilityResult(
            ELIGIBILITY_NEEDS_REVIEW, EligibilityReason.MISSING_APPLY_URL.value
        )

    if len((description or "").strip()) < 80:
        return EligibilityResult(
            ELIGIBILITY_NEEDS_REVIEW, EligibilityReason.THIN_DESCRIPTION.value
        )

    return EligibilityResult(ELIGIBILITY_ACTIONABLE, None)


def eligibility_from_metadata(
    intake_metadata: dict[str, Any] | None,
) -> EligibilityResult:
    if not intake_metadata:
        return EligibilityResult(ELIGIBILITY_ACTIONABLE, None)
    block = intake_metadata.get("eligibility") or {}
    result = block.get("result") or ELIGIBILITY_ACTIONABLE
    reason = block.get("reason_category")
    return EligibilityResult(result, reason)


def attach_eligibility_to_metadata(
    intake_metadata: dict[str, Any] | None,
    eligibility: EligibilityResult,
) -> dict[str, Any]:
    meta = dict(intake_metadata or {})
    meta["eligibility"] = {
        "result": eligibility.result,
        "reason_category": eligibility.reason_category,
        "classified_at": datetime.now(UTC).isoformat(),
    }
    return meta


def resolve_job_eligibility(job: Any) -> EligibilityResult:
    """Use stored eligibility when present; otherwise classify from current job fields."""
    meta = getattr(job, "intake_metadata", None) or {}
    if meta.get("eligibility"):
        return eligibility_from_metadata(meta)
    flags = meta.get("flags") or detect_intake_flags(
        getattr(job, "title", "") or "",
        getattr(job, "description_text", "") or "",
    )
    return classify_job_eligibility(
        title=getattr(job, "title", "") or "",
        description=getattr(job, "description_text", "") or "",
        company_apply_url=getattr(job, "company_apply_url", None),
        intake_flags=flags,
        source_markers=meta.get("simplify_markers"),
    )


def reason_display_label(reason_category: str | None) -> str:
    if not reason_category:
        return ""
    return REASON_LABELS.get(reason_category, reason_category.replace("_", " "))
