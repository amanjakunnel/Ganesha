from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from packages.core.services.job_ranking import detect_intake_flags

ELIGIBILITY_ACTIONABLE = "actionable"
ELIGIBILITY_NEEDS_REVIEW = "needs_review"
ELIGIBILITY_EXCLUDED = "excluded"

# Bump when classification rules change so reassessment can invalidate stale decisions.
ELIGIBILITY_POLICY_VERSION = "eligibility-v2-internship-phd"


class EligibilityReason(str, Enum):
    CLEARANCE_REQUIREMENT = "clearance_requirement"
    CITIZENSHIP_REQUIREMENT = "citizenship_requirement"
    SPONSORSHIP_UNAVAILABLE = "sponsorship_unavailable"
    LISTING_CLOSED = "listing_closed"
    INTERNSHIP_ROLE = "internship_role"
    PHD_REQUIRED = "phd_required"
    PHD_REQUIREMENT_UNCLEAR = "phd_requirement_unclear"
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
    EligibilityReason.INTERNSHIP_ROLE.value: "internship role",
    EligibilityReason.PHD_REQUIRED.value: "PhD required",
    EligibilityReason.PHD_REQUIREMENT_UNCLEAR.value: "degree requirement unclear",
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

# Internship: role/title/employment-type signals — not "internal" / past experience.
_INTERNSHIP_EMPLOYMENT_TYPE = re.compile(
    r"^\s*(internship|intern)\s*$",
    re.IGNORECASE,
)
_INTERNSHIP_TITLE = re.compile(
    r"("
    r"\binternship\b|"
    r"\bintern\b|"
    r"\bintern[- ]level\b"
    r")",
    re.IGNORECASE,
)
_EXPLICIT_INTERNSHIP_ROLE = re.compile(
    r"("
    r"\bthis (?:is|role is|position is) an internship\b|"
    r"\binternship (?:position|role|program|opportunity)\b|"
    r"\b(?:hiring|seeking|looking for) (?:an )?interns?\b|"
    r"\bemployment type\s*:\s*internship\b|"
    r"\bjob type\s*:\s*internship\b"
    r")",
    re.IGNORECASE,
)
_PAST_INTERNSHIP_MENTION = re.compile(
    r"("
    r"prior internship|previous internship|internship experience|"
    r"completed an internship|former intern"
    r")",
    re.IGNORECASE,
)

_PHD_TOKEN = r"(?:ph\.?\s*d\.?|doctorate|doctoral degree)"
_PHD_REQUIRED = re.compile(
    rf"("
    rf"\b{_PHD_TOKEN}\s+required\b|"
    rf"\brequired\s*(?::|-)?\s*{_PHD_TOKEN}\b|"
    rf"\bmust (?:hold|have|possess)\s+(?:a\s+)?{_PHD_TOKEN}\b|"
    rf"\b(?:candidates?|applicants?)\s+must\s+(?:be\s+)?(?:currently\s+)?"
    rf"(?:pursuing|hold|have)\s+(?:a\s+)?{_PHD_TOKEN}\b|"
    rf"\bcurrently pursuing (?:a\s+)?{_PHD_TOKEN}\b|"
    rf"\bminimum (?:education|qualification)s?\s*(?::|-)?\s*{_PHD_TOKEN}\b"
    rf")",
    re.IGNORECASE,
)
_PHD_PREFERRED = re.compile(
    rf"\b{_PHD_TOKEN}\s+(?:preferred|a plus|nice to have|desired)\b|"
    rf"\b(?:preferred|desired)\s*(?::|-)?\s*{_PHD_TOKEN}\b",
    re.IGNORECASE,
)
_PHD_ALTERNATIVE = re.compile(
    rf"("
    rf"\b(?:bachelor'?s|master'?s|bs|ba|ms|m\.?s\.?|mba).{{0,40}}"
    rf"(?:or|/)\s*{_PHD_TOKEN}\b|"
    rf"\b{_PHD_TOKEN}.{{0,40}}(?:or|/)\s*"
    rf"(?:bachelor'?s|master'?s|bs|ba|ms|m\.?s\.?|mba|equivalent)\b|"
    rf"\b{_PHD_TOKEN}\s+or equivalent(?:\s+experience)?\b|"
    rf"\b(?:master'?s|ms|m\.?s\.?).{{0,60}}or\s*{_PHD_TOKEN}\b"
    rf")",
    re.IGNORECASE,
)
_PHD_AMBIGUOUS = re.compile(
    rf"("
    rf"\b{_PHD_TOKEN}\b.{{0,30}}\brequired\b|"
    rf"\brequired\b.{{0,30}}\b{_PHD_TOKEN}\b|"
    rf"\bdoctoral\b.{{0,20}}\brequired\b"
    rf")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EligibilityResult:
    result: str
    reason_category: str | None
    evidence_excerpt: str | None = None

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


def _clip_excerpt(text: str, match: re.Match[str] | None = None, limit: int = 160) -> str:
    if match is not None:
        start = max(0, match.start() - 40)
        end = min(len(text), match.end() + 40)
        snippet = text[start:end].strip()
    else:
        snippet = text.strip()
    snippet = re.sub(r"\s+", " ", snippet)
    if len(snippet) > limit:
        return snippet[: limit - 1] + "…"
    return snippet


def classify_internship_role(
    *,
    title: str,
    description: str,
    employment_type: str | None = None,
) -> EligibilityResult | None:
    """Exclude explicit internship roles; avoid internal/past-experience false positives."""
    emp = (employment_type or "").strip()
    if emp and _INTERNSHIP_EMPLOYMENT_TYPE.match(emp):
        return EligibilityResult(
            ELIGIBILITY_EXCLUDED,
            EligibilityReason.INTERNSHIP_ROLE.value,
            evidence_excerpt=_clip_excerpt(f"employment_type={emp}"),
        )

    title_text = title or ""
    if _INTERNSHIP_TITLE.search(title_text):
        return EligibilityResult(
            ELIGIBILITY_EXCLUDED,
            EligibilityReason.INTERNSHIP_ROLE.value,
            evidence_excerpt=_clip_excerpt(title_text),
        )

    desc = description or ""
    # Do not treat past-internship preferences as the role type.
    if _PAST_INTERNSHIP_MENTION.search(desc) and not _EXPLICIT_INTERNSHIP_ROLE.search(
        desc
    ):
        return None
    match = _EXPLICIT_INTERNSHIP_ROLE.search(desc)
    if match:
        return EligibilityResult(
            ELIGIBILITY_EXCLUDED,
            EligibilityReason.INTERNSHIP_ROLE.value,
            evidence_excerpt=_clip_excerpt(desc, match),
        )
    return None


def classify_phd_requirement(
    *,
    title: str,
    description: str,
) -> EligibilityResult | None:
    """Exclude only mandatory PhD requirements; keep preferred/alternatives open."""
    blob = f"{title or ''}\n{description or ''}"
    if not re.search(r"ph\.?\s*d\.?|doctorate|doctoral", blob, re.IGNORECASE):
        return None

    def _clause_has_alternative(window: str) -> bool:
        return bool(
            _PHD_ALTERNATIVE.search(window)
            or _PHD_PREFERRED.search(window)
            or re.search(
                r"\bor\b.{0,60}(bachelor|master|equivalent|experience)",
                window,
                re.IGNORECASE,
            )
        )

    hard_match: re.Match[str] | None = None
    for match in _PHD_REQUIRED.finditer(blob):
        window = blob[max(0, match.start() - 60) : match.end() + 100]
        if _clause_has_alternative(window):
            continue
        hard_match = match
        break

    if hard_match is not None:
        return EligibilityResult(
            ELIGIBILITY_EXCLUDED,
            EligibilityReason.PHD_REQUIRED.value,
            evidence_excerpt=_clip_excerpt(blob, hard_match),
        )

    # Preferred or explicit alternative paths are not exclusions.
    if _PHD_PREFERRED.search(blob) or _PHD_ALTERNATIVE.search(blob):
        return None

    ambiguous = _PHD_AMBIGUOUS.search(blob)
    if ambiguous:
        return EligibilityResult(
            ELIGIBILITY_NEEDS_REVIEW,
            EligibilityReason.PHD_REQUIREMENT_UNCLEAR.value,
            evidence_excerpt=_clip_excerpt(blob, ambiguous),
        )
    return None


def classify_job_eligibility(
    *,
    title: str,
    description: str,
    company_apply_url: str | None = None,
    location: str | None = None,
    employment_type: str | None = None,
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

    internship = classify_internship_role(
        title=title, description=description, employment_type=employment_type
    )
    if internship is not None:
        return internship

    phd = classify_phd_requirement(title=title, description=description)
    if phd is not None:
        return phd

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
    excerpt = block.get("evidence_excerpt")
    return EligibilityResult(result, reason, excerpt)


def attach_eligibility_to_metadata(
    intake_metadata: dict[str, Any] | None,
    eligibility: EligibilityResult,
) -> dict[str, Any]:
    meta = dict(intake_metadata or {})
    block: dict[str, Any] = {
        "result": eligibility.result,
        "reason_category": eligibility.reason_category,
        "classified_at": datetime.now(UTC).isoformat(),
        "policy_version": ELIGIBILITY_POLICY_VERSION,
    }
    if eligibility.evidence_excerpt:
        block["evidence_excerpt"] = eligibility.evidence_excerpt
    meta["eligibility"] = block
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
        employment_type=getattr(job, "employment_type", None),
        intake_flags=flags,
        source_markers=meta.get("simplify_markers"),
    )


def reason_display_label(reason_category: str | None) -> str:
    if not reason_category:
        return ""
    return REASON_LABELS.get(reason_category, reason_category.replace("_", " "))


def reassess_eligibility(
    session: Session,
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Recompute and optionally persist eligibility for stored jobs.

    Does not delete jobs, applications, notes, or generated artifacts.
    Idempotent for a fixed policy version: repeated applies rewrite the same decision.
    Live readers already call resolve_job_eligibility; this updates persisted metadata
    and stamps policy_version so stale decisions are visible/invalidated.
    """
    from packages.core.domain.models import JobPosting

    q = session.query(JobPosting).order_by(JobPosting.created_at.desc())
    if limit is not None:
        q = q.limit(limit)
    jobs = q.all()

    result_counts: dict[str, int] = {
        ELIGIBILITY_ACTIONABLE: 0,
        ELIGIBILITY_NEEDS_REVIEW: 0,
        ELIGIBILITY_EXCLUDED: 0,
    }
    reason_counts: dict[str, int] = {}
    changed = 0
    stale_policy = 0

    for job in jobs:
        previous = eligibility_from_metadata(job.intake_metadata)
        prior_version = ((job.intake_metadata or {}).get("eligibility") or {}).get(
            "policy_version"
        )
        if prior_version != ELIGIBILITY_POLICY_VERSION:
            stale_policy += 1
        current = resolve_job_eligibility(job)
        result_counts[current.result] = result_counts.get(current.result, 0) + 1
        if current.reason_category:
            reason_counts[current.reason_category] = (
                reason_counts.get(current.reason_category, 0) + 1
            )
        if (
            previous.result != current.result
            or previous.reason_category != current.reason_category
            or prior_version != ELIGIBILITY_POLICY_VERSION
        ):
            changed += 1
        if not dry_run:
            meta = dict(job.intake_metadata or {})
            job.intake_metadata = attach_eligibility_to_metadata(meta, current)
            session.add(job)

    if not dry_run:
        session.flush()

    return {
        "scanned": len(jobs),
        "dry_run": dry_run,
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "changed": changed,
        "stale_policy": stale_policy,
        "result_counts": result_counts,
        "reason_counts": dict(sorted(reason_counts.items())),
    }
