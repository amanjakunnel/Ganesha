from __future__ import annotations

import re
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
    NON_US_LOCATION = "non_us_location"
    LOCATION_UNSPECIFIED = "location_unspecified"
    SENIORITY_MISMATCH = "seniority_mismatch"
    MISSING_APPLY_URL = "missing_apply_url"
    THIN_DESCRIPTION = "thin_description"
    INCOMPLETE_SOURCE_ROW = "incomplete_source_row"


REASON_LABELS: dict[str, str] = {
    EligibilityReason.CLEARANCE_REQUIREMENT.value: "clearance requirement",
    EligibilityReason.CITIZENSHIP_REQUIREMENT.value: "citizenship requirement",
    EligibilityReason.SPONSORSHIP_UNAVAILABLE.value: "sponsorship unavailable",
    EligibilityReason.LISTING_CLOSED.value: "listing closed",
    EligibilityReason.NON_US_LOCATION.value: "outside United States",
    EligibilityReason.LOCATION_UNSPECIFIED.value: "location unspecified",
    EligibilityReason.SENIORITY_MISMATCH.value: "seniority mismatch",
    EligibilityReason.MISSING_APPLY_URL.value: "missing apply URL",
    EligibilityReason.THIN_DESCRIPTION.value: "thin description",
    EligibilityReason.INCOMPLETE_SOURCE_ROW.value: "incomplete source row",
}

# Operator applies only in the United States.
_US_COUNTRY = re.compile(
    r"\b(united states|u\.?\s*s\.?\s*a\.?|u\.?\s*s\.?)\b",
    re.IGNORECASE,
)
_US_STATES = re.compile(
    r"\b("
    r"alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|"
    r"florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|"
    r"louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|"
    r"missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|"
    r"new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|"
    r"rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|"
    r"virginia|washington|west virginia|wisconsin|wyoming|"
    r"district of columbia|washington,? d\.?c\.?"
    r")\b",
    re.IGNORECASE,
)
_US_STATE_ABBR = re.compile(
    r"(?:^|[\s,;/|(])("
    r"AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|"
    r"MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|"
    r"WI|WY|DC"
    r")(?:$|[\s,;/)|])"
)
_NON_US_COUNTRY = re.compile(
    r"\b("
    r"canada|united kingdom|england|scotland|wales|ireland|germany|france|"
    r"india|singapore|australia|japan|china|netherlands|switzerland|mexico|"
    r"brazil|israel|korea|south korea|philippines|poland|sweden|norway|denmark|"
    r"spain|italy|portugal|new zealand|uae|dubai|hong kong|taiwan|vietnam|"
    r"thailand|indonesia|malaysia|pakistan|bangladesh|nigeria|south africa|"
    r"argentina|chile|colombia|peru|turkey|greece|austria|belgium|finland|"
    r"romania|hungary|ukraine|russia|czech|slovakia|croatia|serbia|egypt|"
    r"saudi arabia|qatar|kuwait|ireland|northern ireland"
    r")\b",
    re.IGNORECASE,
)
_CANADA_REGION = re.compile(
    r"\b("
    r"british columbia|ontario|quebec|alberta|manitoba|saskatchewan|"
    r"nova scotia|new brunswick|newfoundland|prince edward island|"
    r"yukon|nunavut|northwest territories|"
    r"vancouver|toronto|montreal|ottawa|calgary|edmonton|winnipeg"
    r")\b",
    re.IGNORECASE,
)
# Province abbreviations only when tokenized like ", BC" / "(ON)" — not bare words.
_CANADA_PROVINCE_ABBR = re.compile(
    r"(?:^|[\s,;/|(])(BC|ON|QC|AB|MB|SK|NS|NB|PE|NL|YT|NT|NU)(?:$|[\s,;/)|])"
)
_REMOTE = re.compile(r"\bremote\b", re.IGNORECASE)


@dataclass(frozen=True)
class EligibilityResult:
    result: str
    reason_category: str | None

    def is_actionable(self) -> bool:
        return self.result == ELIGIBILITY_ACTIONABLE


def location_has_us_signal(location: str) -> bool:
    return bool(
        _US_COUNTRY.search(location)
        or _US_STATES.search(location)
        or _US_STATE_ABBR.search(location)
    )


def location_has_non_us_signal(location: str) -> bool:
    text = location.strip()
    if _NON_US_COUNTRY.search(text):
        return True
    # Canadian city/province alone without US markers (e.g. "Vancouver, BC")
    if location_has_us_signal(text):
        return False
    if _CANADA_REGION.search(text) or _CANADA_PROVINCE_ABBR.search(text):
        return True
    return False


def classify_location_for_us_applicant(location: str | None) -> EligibilityResult | None:
    """Return an eligibility override for location, or None if location is fine.

    Policy: operator can only apply in the United States.
    - US city/state/country (including multi-city lists that include US) → OK
    - Explicit non-US only → excluded
    - Missing/blank → needs review
    - Bare remote without country → OK (treated as US-capable remote)
    """
    if location is None or not str(location).strip():
        return EligibilityResult(
            ELIGIBILITY_NEEDS_REVIEW, EligibilityReason.LOCATION_UNSPECIFIED.value
        )

    text = str(location).strip()
    has_us = location_has_us_signal(text)
    has_non_us = location_has_non_us_signal(text)

    if has_us:
        # Multi-location postings that include US remain actionable.
        return None
    if has_non_us:
        return EligibilityResult(
            ELIGIBILITY_EXCLUDED, EligibilityReason.NON_US_LOCATION.value
        )
    if _REMOTE.search(text):
        return None
    # Unrecognized place names without country/state → review, don't guess.
    return EligibilityResult(
        ELIGIBILITY_NEEDS_REVIEW, EligibilityReason.LOCATION_UNSPECIFIED.value
    )


def classify_job_eligibility(
    *,
    title: str,
    description: str,
    company_apply_url: str | None = None,
    location: str | None = None,
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

    location_result = classify_location_for_us_applicant(location)
    if location_result is not None:
        return location_result

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
    """Reclassify from current job fields so policy changes apply immediately."""
    meta = getattr(job, "intake_metadata", None) or {}
    flags = meta.get("flags") or detect_intake_flags(
        getattr(job, "title", "") or "",
        getattr(job, "description_text", "") or "",
    )
    return classify_job_eligibility(
        title=getattr(job, "title", "") or "",
        description=getattr(job, "description_text", "") or "",
        company_apply_url=getattr(job, "company_apply_url", None),
        location=getattr(job, "location", None),
        intake_flags=flags,
        source_markers=meta.get("simplify_markers"),
    )


def reason_display_label(reason_category: str | None) -> str:
    if not reason_category:
        return ""
    return REASON_LABELS.get(reason_category, reason_category.replace("_", " "))
