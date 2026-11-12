from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from packages.core.services.job_ranking import detect_intake_flags

ELIGIBILITY_ACTIONABLE = "actionable"
ELIGIBILITY_NEEDS_REVIEW = "needs_review"
ELIGIBILITY_EXCLUDED = "excluded"

# Bump when classification rules change so reassessment can invalidate stale decisions.
ELIGIBILITY_POLICY_VERSION = "eligibility-v3-enrichment-citizenship"

STUB_DESCRIPTION_MAX_CHARS = 450
SIMPLIFY_STUB_SOURCE = "github.com/SimplifyJobs"


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
    DESCRIPTION_UNVERIFIED = "description_unverified"
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
    EligibilityReason.DESCRIPTION_UNVERIFIED.value: "job description not verified",
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
    r"^\s*(internship|intern|co-?op|co op)\s*$",
    re.IGNORECASE,
)
_EXPLICIT_INTERNSHIP_ROLE = re.compile(
    r"("
    r"\bthis (?:is|role is|position is) an internship\b|"
    r"\binternship (?:position|role|program|opportunity)\b|"
    r"\b(?:hiring|seeking|looking for) (?:an )?interns?\b|"
    r"\bemployment type\s*:\s*(?:internship|intern|co-?op)\b|"
    r"\bjob type\s*:\s*(?:internship|intern|co-?op)\b|"
    r"^\s*role\s*:\s*.*\b(?:intern(?:ship)?s?|co-?op)\b"
    r")",
    re.IGNORECASE | re.MULTILINE,
)
_PAST_INTERNSHIP_MENTION = re.compile(
    r"("
    r"prior internship|previous internship|internship experience|"
    r"completed an internship|former intern|"
    r"internships?\s+(?:are|is)\s+(?:a\s+)?plus"
    r")",
    re.IGNORECASE,
)
_PHD_TOKEN = r"(?:ph\.?\s*d\.?|doctorate|doctoral degree)"
_PHD_TITLE_PROGRAM = re.compile(
    rf"("
    rf"\b{_PHD_TOKEN}\b.{{0,40}}\b(?:new college grad|new grad|college grad|"
    rf"graduate|students?|candidates?)\b|"
    rf"\b(?:new college grad|new grad|college grad|graduate|students?|"
    rf"candidates?)\b.{{0,40}}\b{_PHD_TOKEN}\b|"
    rf"\b{_PHD_TOKEN}\s+new\b|"
    rf"\bcurrently pursuing\b.{{0,30}}\b{_PHD_TOKEN}\b|"
    rf"\b{_PHD_TOKEN}\s+students?\b"
    rf")",
    re.IGNORECASE,
)
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

# Work authorization / clearance — hard excludes when clearly required.
_EEO_BOILERPLATE = re.compile(
    r"("
    r"equal employment opportunity|eeo\b|affirmative action|"
    r"does not discriminate|protected veteran|disability status|"
    r"regardless of .{0,40}citizenship|"
    r"applicants? (?:will|shall) receive consideration|"
    r"all qualified applicants"
    r")",
    re.IGNORECASE,
)
_CITIZENSHIP_NEGATION = re.compile(
    r"("
    r"does not require (?:u\.?s\.?\s+)?citizenship|"
    r"no (?:u\.?s\.?\s+)?citizenship (?:required|necessary)|"
    r"citizenship (?:is )?not required|"
    r"without regard to .{0,60}citizenship"
    r")",
    re.IGNORECASE,
)
_CLEARANCE_PREFERRED = re.compile(
    r"("
    r"clearance (?:preferred|a plus|nice to have|desired)|"
    r"(?:preferred|desired).{0,20}clearance|"
    r"ability to obtain.{0,40}clearance"
    r")",
    re.IGNORECASE,
)
_CITIZENSHIP_HARD = re.compile(
    r"("
    r"\bmust be (?:a )?(?:u\.?s\.?\s+)?citizen\b|"
    r"\b(?:u\.?s\.?\s+)?citizenship required\b|"
    r"\bonly (?:u\.?s\.?\s+)?citizens?\b|"
    r"\b(?:u\.?s\.?\s+)?citizens? (?:and|&) (?:permanent residents?|green card)\s+only\b|"
    r"\b(?:permanent resident|green card) (?:status )?required\b|"
    r"\bus person(?:s)? (?:only|required|status)\b|"
    r"\bitar\b.{0,40}\b(?:us|u\.s\.)\s*person|"
    r"\b(?:us|u\.s\.)\s*person.{0,40}\bitar\b|"
    r"\brestricted to (?:u\.?s\.?\s+)?citizens?\b"
    r")",
    re.IGNORECASE,
)
_CLEARANCE_HARD = re.compile(
    r"("
    r"\bactive (?:ts/?sci|top secret|secret)(?:\s+clearance)?\b|"
    r"\b(?:must|required to) (?:have|hold|possess).{0,30}clearance\b|"
    r"\bsecurity clearance required\b|"
    r"\brequires (?:an? )?(?:active )?security clearance\b|"
    r"\bcurrent (?:ts/?sci|top secret|secret) clearance\b"
    r")",
    re.IGNORECASE,
)
_SPONSORSHIP_HARD = re.compile(
    r"("
    r"\b(?:will|can|do|does) not sponsor\b|"
    r"\bunable to sponsor\b|"
    r"\bno sponsorship\b|"
    r"\bnot (?:be )?sponsoring\b|"
    r"\bwithout sponsorship\b|"
    r"\bmust (?:already )?be (?:authorized|eligible) to work.{0,40}"
    r"(?:without|no) sponsorship\b|"
    r"\bauthorized to work.{0,60}without (?:current or future )?sponsorship\b|"
    r"\bno visa sponsorship\b|"
    r"\bsponsorship (?:is )?(?:not|un)available\b"
    r")",
    re.IGNORECASE,
)
_SPONSORSHIP_AMBIGUOUS = re.compile(
    r"("
    r"\bopt\b|\bcpt\b|\bstem opt\b|"
    r"\bcase[- ]by[- ]case\b|"
    r"\bsponsorship (?:considered|available|may be|possible)\b|"
    r"\bwe (?:do )?sponsor\b|"
    r"\bh-?1b\b|"
    r"\bvisa (?:support|sponsorship)\b|"
    r"\bwork authorization\b"
    r")",
    re.IGNORECASE,
)
_SPONSORSHIP_OFFERED = re.compile(
    r"("
    r"\bvisa sponsorship (?:is )?(?:available|offered|provided)\b|"
    r"\bwe (?:will|can|do) sponsor\b|"
    r"\bsponsorship available\b"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EligibilityResult:
    result: str
    reason_category: str | None
    evidence_excerpt: str | None = None
    flags: tuple[str, ...] = field(default_factory=tuple)
    flag_excerpts: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def is_actionable(self) -> bool:
        return self.result == ELIGIBILITY_ACTIONABLE


def is_stub_description(description: str | None) -> bool:
    """True when text looks like an aggregator stub rather than a full JD."""
    text = (description or "").strip()
    if not text:
        return True
    if len(text) < 80:
        return True
    simplify_stub = SIMPLIFY_STUB_SOURCE.lower() in text.lower()
    looks_like_row_stub = bool(
        re.match(
            r"(?is)^company:\s*.+\nrole:\s*.+",
            text,
        )
    )
    # SimplifyJobs README rows are short structured stubs, not full postings.
    if simplify_stub and len(text) <= STUB_DESCRIPTION_MAX_CHARS:
        return True
    if (
        simplify_stub
        and looks_like_row_stub
        and len(text) <= STUB_DESCRIPTION_MAX_CHARS * 2
    ):
        return True
    # Very short text without a real sentence body.
    if len(text) < 120 and not re.search(r"[.!?].{30,}", text):
        return True
    return False


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


def _window_is_boilerplate(text: str, match: re.Match[str]) -> bool:
    start = max(0, match.start() - 220)
    end = min(len(text), match.end() + 220)
    window = text[start:end]
    return bool(_EEO_BOILERPLATE.search(window) or _CITIZENSHIP_NEGATION.search(window))


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
    # Word-boundary match avoids "Internal Tools" false positives.
    if re.search(r"\bintern(?:ship)?s?\b|\bco-?op\b", title_text, re.I):
        return EligibilityResult(
            ELIGIBILITY_EXCLUDED,
            EligibilityReason.INTERNSHIP_ROLE.value,
            evidence_excerpt=_clip_excerpt(title_text),
        )

    desc = description or ""
    # Aggregator stub: "Role: … Intern …"
    stub_role = re.search(
        r"(?im)^\s*role\s*:\s*(.+)$",
        desc,
    )
    if stub_role and re.search(
        r"\bintern(?:ship)?s?\b|\bco-?op\b", stub_role.group(1), re.I
    ):
        return EligibilityResult(
            ELIGIBILITY_EXCLUDED,
            EligibilityReason.INTERNSHIP_ROLE.value,
            evidence_excerpt=_clip_excerpt(stub_role.group(0)),
        )

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
    title_text = title or ""
    desc = description or ""
    blob = f"{title_text}\n{desc}"
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

    # Title-only program forms: "PhD New College Grad", "Ph.D. students", etc.
    title_match = _PHD_TITLE_PROGRAM.search(title_text)
    if title_match and not _clause_has_alternative(title_text):
        return EligibilityResult(
            ELIGIBILITY_EXCLUDED,
            EligibilityReason.PHD_REQUIRED.value,
            evidence_excerpt=_clip_excerpt(title_text, title_match),
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


def classify_work_authorization(
    *,
    title: str,
    description: str,
    source_markers: dict[str, bool] | None = None,
    description_verified: bool = True,
) -> EligibilityResult | None:
    """Citizenship / clearance / sponsorship rules for F-1-compatible policy.

    Full text wins over source tags. Unverified stubs do not exclude on tags alone.
    Ambiguous / OPT-compatible language → include with visible flags + excerpts.
    Never embeds immigration status labels into generated documents (caller concern).
    """
    markers = source_markers or {}
    blob = f"{title or ''}\n{description or ''}"
    flags: list[str] = []
    excerpts: list[tuple[str, str]] = []
    conflicts: list[str] = []

    # Clearance hard requirements (skip preferred / obtain-later).
    # Apply even on stubs when the available text itself states the requirement.
    for match in _CLEARANCE_HARD.finditer(blob):
        if _CLEARANCE_PREFERRED.search(
            blob[max(0, match.start() - 80) : match.end() + 80]
        ):
            continue
        return EligibilityResult(
            ELIGIBILITY_EXCLUDED,
            EligibilityReason.CLEARANCE_REQUIREMENT.value,
            evidence_excerpt=_clip_excerpt(blob, match),
        )

    # Citizenship / ITAR / US-person hard requirements.
    for match in _CITIZENSHIP_HARD.finditer(blob):
        if _window_is_boilerplate(blob, match):
            continue
        if _CITIZENSHIP_NEGATION.search(
            blob[max(0, match.start() - 120) : match.end() + 120]
        ):
            continue
        return EligibilityResult(
            ELIGIBILITY_EXCLUDED,
            EligibilityReason.CITIZENSHIP_REQUIREMENT.value,
            evidence_excerpt=_clip_excerpt(blob, match),
        )

    # Sponsorship unavailable hard requirements (also honor explicit stub text).
    sponsorship_hard = None
    for match in _SPONSORSHIP_HARD.finditer(blob):
        if _window_is_boilerplate(blob, match):
            continue
        sponsorship_hard = match
        break

    sponsorship_offered = _SPONSORSHIP_OFFERED.search(blob)
    ambiguous = list(_SPONSORSHIP_AMBIGUOUS.finditer(blob))

    if not description_verified:
        # Explicit "no sponsorship" / hard wording in the stub still excludes.
        if sponsorship_hard and not sponsorship_offered and not ambiguous:
            return EligibilityResult(
                ELIGIBILITY_EXCLUDED,
                EligibilityReason.SPONSORSHIP_UNAVAILABLE.value,
                evidence_excerpt=_clip_excerpt(blob, sponsorship_hard),
            )
        flags.append("sponsorship_unknown")
        flags.append("description_not_verified")
        # Tags are hints only when JD text is missing / unverified.
        if markers.get("citizenship_required"):
            flags.append("source_tag_citizenship")
            conflicts.append("tag_citizenship_unverified_text")
        if markers.get("no_sponsorship"):
            flags.append("source_tag_no_sponsorship")
            conflicts.append("tag_sponsorship_unverified_text")
        return EligibilityResult(
            ELIGIBILITY_NEEDS_REVIEW,
            EligibilityReason.DESCRIPTION_UNVERIFIED.value,
            evidence_excerpt="Job description not verified; sponsorship unknown",
            flags=tuple(dict.fromkeys(flags)),
            flag_excerpts=tuple(excerpts),
        )

    tag_no_sponsor = bool(markers.get("no_sponsorship"))
    tag_citizen = bool(markers.get("citizenship_required"))

    if sponsorship_hard and sponsorship_offered:
        conflicts.append("sponsorship_text_conflict")
        flags.append("sponsorship_conflict")
        excerpts.append(
            ("sponsorship_conflict", _clip_excerpt(blob, sponsorship_hard))
        )
        excerpts.append(
            ("sponsorship_offered", _clip_excerpt(blob, sponsorship_offered))
        )
    elif sponsorship_hard:
        # Tag agrees or is absent — exclude on clear text.
        if tag_no_sponsor is False and ambiguous:
            # Hard "no sponsor" plus OPT/case-by-case elsewhere → flag, include.
            flags.append("sponsorship_ambiguous")
            excerpts.append(
                ("sponsorship_hard", _clip_excerpt(blob, sponsorship_hard))
            )
            for match in ambiguous[:2]:
                excerpts.append(
                    ("sponsorship_ambiguous", _clip_excerpt(blob, match))
                )
        else:
            return EligibilityResult(
                ELIGIBILITY_EXCLUDED,
                EligibilityReason.SPONSORSHIP_UNAVAILABLE.value,
                evidence_excerpt=_clip_excerpt(blob, sponsorship_hard),
            )
    elif tag_no_sponsor:
        # Tag says no sponsorship but text does not confirm — or text contradicts.
        if sponsorship_offered:
            conflicts.append("tag_no_sponsorship_vs_text_offers")
            flags.append("sponsorship_conflict")
            excerpts.append(
                ("source_tag", "source tag indicated no sponsorship")
            )
            excerpts.append(
                ("sponsorship_offered", _clip_excerpt(blob, sponsorship_offered))
            )
        else:
            # Hint only without confirming text → flag, do not exclude.
            flags.append("source_tag_no_sponsorship")
            flags.append("sponsorship_unconfirmed")
            excerpts.append(
                ("source_tag", "source tag indicated no sponsorship (unconfirmed)")
            )

    if tag_citizen:
        # If text already excluded above, we never get here. Tag without text → flag.
        if not _CITIZENSHIP_HARD.search(blob):
            flags.append("source_tag_citizenship")
            flags.append("citizenship_unconfirmed")
            excerpts.append(
                ("source_tag", "source tag indicated US citizenship (unconfirmed)")
            )
            conflicts.append("tag_citizenship_without_confirming_text")

    if ambiguous and "sponsorship_ambiguous" not in flags and not sponsorship_hard:
        flags.append("sponsorship_ambiguous")
        for match in ambiguous[:3]:
            excerpts.append(("sponsorship_ambiguous", _clip_excerpt(blob, match)))

    if sponsorship_offered and "sponsorship_conflict" not in flags:
        flags.append("sponsorship_offered")
        excerpts.append(
            ("sponsorship_offered", _clip_excerpt(blob, sponsorship_offered))
        )

    if conflicts:
        flags.append("source_text_conflict")

    if flags:
        return EligibilityResult(
            ELIGIBILITY_ACTIONABLE,
            None,
            evidence_excerpt=excerpts[0][1] if excerpts else None,
            flags=tuple(dict.fromkeys(flags)),
            flag_excerpts=tuple(excerpts),
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
    description_verified: bool | None = None,
) -> EligibilityResult:
    """Deterministic eligibility classification without echoing sensitive posting text."""
    flags = intake_flags or detect_intake_flags(title, description)
    markers = source_markers or {}
    verified = (
        (not is_stub_description(description))
        if description_verified is None
        else description_verified
    )

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

    work_auth = classify_work_authorization(
        title=title,
        description=description,
        source_markers=markers,
        description_verified=verified,
    )
    if work_auth is not None and work_auth.result == ELIGIBILITY_EXCLUDED:
        return work_auth
    if work_auth is not None and work_auth.reason_category == (
        EligibilityReason.DESCRIPTION_UNVERIFIED.value
    ):
        # Unverified JD: keep stored/auditable as needs_review; not Recommended.
        return work_auth

    # Legacy clearance flag from intake ranking patterns — only when verified text.
    if verified and flags.get("clearance_concern") and (
        work_auth is None or work_auth.result != ELIGIBILITY_EXCLUDED
    ):
        # detect_intake_flags still matches broad citizenship phrases; prefer
        # dedicated classifier. Only fall back when patterns hit and classifier missed.
        blob = f"{title}\n{description}"
        if _CLEARANCE_HARD.search(blob) and not _CLEARANCE_PREFERRED.search(blob):
            return EligibilityResult(
                ELIGIBILITY_EXCLUDED, EligibilityReason.CLEARANCE_REQUIREMENT.value
            )

    location_result = classify_location_for_us_applicant(location)
    if location_result is not None:
        # Preserve work-auth flags on location overrides when actionable flags exist.
        if work_auth and work_auth.flags and location_result.result != ELIGIBILITY_EXCLUDED:
            return EligibilityResult(
                location_result.result,
                location_result.reason_category,
                location_result.evidence_excerpt,
                flags=work_auth.flags,
                flag_excerpts=work_auth.flag_excerpts,
            )
        return location_result

    if flags.get("seniority_concern"):
        return EligibilityResult(
            ELIGIBILITY_NEEDS_REVIEW,
            EligibilityReason.SENIORITY_MISMATCH.value,
            flags=work_auth.flags if work_auth else (),
            flag_excerpts=work_auth.flag_excerpts if work_auth else (),
        )

    if not (company_apply_url or "").strip():
        return EligibilityResult(
            ELIGIBILITY_NEEDS_REVIEW, EligibilityReason.MISSING_APPLY_URL.value
        )

    if len((description or "").strip()) < 80:
        return EligibilityResult(
            ELIGIBILITY_NEEDS_REVIEW, EligibilityReason.THIN_DESCRIPTION.value
        )

    if work_auth is not None:
        return EligibilityResult(
            ELIGIBILITY_ACTIONABLE,
            None,
            evidence_excerpt=work_auth.evidence_excerpt,
            flags=work_auth.flags,
            flag_excerpts=work_auth.flag_excerpts,
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
    flags = tuple(block.get("flags") or ())
    raw_excerpts = block.get("flag_excerpts") or []
    flag_excerpts: list[tuple[str, str]] = []
    for item in raw_excerpts:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            flag_excerpts.append((str(item[0]), str(item[1])))
        elif isinstance(item, dict) and "flag" in item and "excerpt" in item:
            flag_excerpts.append((str(item["flag"]), str(item["excerpt"])))
    return EligibilityResult(result, reason, excerpt, flags, tuple(flag_excerpts))


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
    if eligibility.flags:
        block["flags"] = list(eligibility.flags)
    if eligibility.flag_excerpts:
        block["flag_excerpts"] = [
            {"flag": f, "excerpt": e} for f, e in eligibility.flag_excerpts
        ]
    meta["eligibility"] = block
    return meta


def _job_description_verified(job: Any) -> bool:
    meta = getattr(job, "intake_metadata", None) or {}
    enrichment = meta.get("enrichment") or {}
    if enrichment.get("status") == "enriched" and enrichment.get(
        "description_verified", True
    ):
        return True
    text = getattr(job, "description_text", "") or ""
    return not is_stub_description(text)


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
        description_verified=_job_description_verified(job),
    )


def reason_display_label(reason_category: str | None) -> str:
    if not reason_category:
        return ""
    return REASON_LABELS.get(reason_category, reason_category.replace("_", " "))


def enrichment_badge(job: Any) -> str | None:
    """UI badge when JD is stub or enrichment failed/unavailable."""
    meta = getattr(job, "intake_metadata", None) or {}
    enrichment = meta.get("enrichment") or {}
    status = enrichment.get("status")
    if status in {"failed", "unavailable"}:
        return "Job description not verified"
    if is_stub_description(getattr(job, "description_text", "") or ""):
        return "Job description not verified"
    return None


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
            or previous.flags != current.flags
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
