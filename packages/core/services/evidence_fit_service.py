"""Evidence-based job fit, separate from eligibility and queue ranking priority."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from packages.core.domain.models import JobPosting, ResumeFact, hash_text
from packages.core.settings import settings

FIT_POLICY_VERSION = "evidence-fit-v3"

BAND_RECOMMENDED = "recommended"
BAND_STRETCH = "stretch"
BAND_LOW_FIT = "low_fit"
BAND_NEEDS_REVIEW = "needs_review"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_INSUFFICIENT = "insufficient"

STATUS_DEMONSTRATED = "demonstrated"
STATUS_ADJACENT = "adjacent"
STATUS_NOT_DEMONSTRATED = "not_demonstrated"
STATUS_UNKNOWN = "unknown"

# Specialist domains: decisive mismatch only when multiple core signals fire
# and the posting is not clearly software tooling / methods / infrastructure.
DOMAIN_PROFILES: dict[str, dict[str, Any]] = {
    "hardware_formal_verification": {
        "label": "hardware RTL / formal verification",
        "core": (
            r"\brtl\b",
            r"\bverilog\b",
            r"\bsystemverilog\b",
            r"\bsva\b",
            r"systemverilog assertion",
            r"\basic\b",
            r"\bsoc\b",
            r"formal verification",
            r"hardware formal",
            r"digital design",
            r"uvm\b",
            r"\bfpgas?\b",
            r"gate[- ]level",
            r"timing closure",
        ),
        "min_core_hits": 3,
        "software_context": (
            r"software engineer",
            r"backend",
            r"compiler",
            r"tooling",
            r"eda tool",
            r"formal methods",
            r"static analysis",
            r"infrastructure",
            r"platform engineer",
            r"developer tools",
            r"python",
            r"distributed system",
        ),
    },
    "fpga_hardware_design": {
        "label": "FPGA / digital hardware design",
        "core": (
            r"\bfpga\b",
            r"\brtl\b",
            r"\bverilog\b",
            r"\bvhdl\b",
            r"\bhdl\b",
            r"synthesis",
            r"place and route",
            r"bitstream",
        ),
        "min_core_hits": 3,
        "software_context": (
            r"software engineer",
            r"firmware driver",
            r"embedded software",
            r"linux driver",
            r"userspace",
            r"tooling",
        ),
    },
    "analog_rf_hardware": {
        "label": "analog / RF hardware",
        "core": (
            r"\brf\b",
            r"analog circuit",
            r"pcb layout",
            r"schematic capture",
            r"spice\b",
            r"antenna",
            r"mixed[- ]signal",
        ),
        "min_core_hits": 3,
        "software_context": (
            r"software",
            r"firmware",
            r"protocol stack",
            r"backend",
        ),
    },
}

# Common languages/platforms alone are not enough for a strong specialty match.
GENERIC_SKILLS = {
    "python",
    "java",
    "c++",
    "c",
    "linux",
    "git",
    "aws",
    "azure",
    "sql",
    "docker",
    "engineering",
    "software",
    "computer",
    "science",
}

# Action/filler words that appear in almost every JD — never "demonstrate" a
# core capability by themselves (e.g. "design" matching any design work).
GENERIC_ACTION_FILLER = {
    "design",
    "designs",
    "designed",
    "designing",
    "develop",
    "develops",
    "developing",
    "development",
    "deploying",
    "years",
    "developer",
    "developers",
    "build",
    "builds",
    "built",
    "implement",
    "implements",
    "implementation",
    "maintain",
    "maintains",
    "maintenance",
    "lead",
    "leads",
    "leading",
    "own",
    "owns",
    "ownership",
    "apply",
    "applies",
    "applied",
    "investigate",
    "troubleshoot",
    "resolve",
    "conduct",
    "provide",
    "provides",
    "mentor",
    "mentoring",
    "define",
    "enhance",
    "support",
    "supporting",
    "including",
    "system",
    "systems",
    "data",
    "application",
    "applications",
    "processor",
    "processors",
    "capability",
    "capabilities",
    "feature",
    "features",
    "practice",
    "practices",
    "engineer",
    "engineers",
    "experienced",
    "complex",
    "advanced",
    "technical",
    "operational",
    "production",
    "testing",
    "test",
    "monitoring",
    "incident",
    "incidents",
    "schema",
    "schemas",
    "database",
    "databases",
    "backend",
    "frontend",
    "platform",
    "platforms",
    "workflow",
    "workflows",
    "process",
    "processes",
    "quality",
    "reliability",
    "service",
    "services",
    "code",
    "reviews",
    "guidance",
    "collaboration",
    "knowledge",
    "sharing",
    "best",
    "less",
    "other",
    "new",
    "existing",
    "easy",
    "easily",
    "deployed",
    "monitored",
    "validated",
    "transform",
    "distribute",
    "ingest",
    "source",
    "through",
    "used",
    "using",
    "based",
    "software",
    "engineering",
    "computer",
    "science",
    "skills",
    "skill",
    "processing",
    "concepts",
    "concept",
    "efficiency",
    "objects",
    "object",
    "end-to-end",
    "applying",
    "experience",
    "experiences",
    "distributed",
    "functional",
    "programming",
    "environments",
    "environment",
    "with",
}

SENIORITY_MARKERS = (
    r"\b(10\+|8\+|7\+|5\+|senior|staff|principal|architect|lead engineer)\b",
    r"\b(lead|own|ownership of)\b.{0,40}\b(team|roadmap|architecture)\b",
)


@dataclass
class RequirementEvidence:
    requirement: str
    classification: str  # required | preferred | unknown
    supporting_fact_ids: list[str] = field(default_factory=list)
    evidence_status: str = STATUS_UNKNOWN
    importance: str = "medium"  # high | medium | low
    explanation: str = ""


@dataclass
class EvidenceFitAssessment:
    band: str
    score: int
    confidence: str
    short_reason: str
    domain_mismatch: bool = False
    domain_mismatch_reason: str | None = None
    requirements: list[RequirementEvidence] = field(default_factory=list)
    role_family: str | None = None
    policy_version: str = FIT_POLICY_VERSION
    candidate_evidence_hash: str = ""
    job_description_hash: str = ""
    assessed_at: str = ""
    eligible_for_default_feed: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


_LOCATION_NOISE = frozenset(
    {
        "state",
        "college",
        "university",
        "campus",
        "location",
        "remote",
        "hybrid",
        "onsite",
        "office",
        "headquarters",
        "hq",
        "united",
        "states",
        "usa",
        "pennsylvania",
        "park",
        "city",
        "open",
        "hiring",
        "candidates",
        "work",
        "from",
        "our",
        "global",
        "or",
        "remotely",
    }
)

_MATCH_STOP = frozenset(
    {
        "and",
        "the",
        "with",
        "for",
        "you",
        "will",
        "our",
        "this",
        "that",
        "from",
        "have",
        "are",
        "ability",
        "experience",
        "strong",
        "using",
        "years",
        "work",
        "team",
        *_LOCATION_NOISE,
    }
)

_LOCATION_LINE = re.compile(
    r"(?:"
    r"\b(remote|hybrid|on[- ]?site|work from home|wfh)\b|"
    r"\b(headquarters|hq|campus|office)\b|"
    r"\b(united states|u\.s\.a?\.?|usa)\b|"
    r"\b[A-Z][a-z]+(?: [A-Z][a-z]+)*,\s*[A-Z]{2}\b|"
    r"\bwe are open to hiring\b|"
    r"\b(located in|based in|office in)\b"
    r")",
    re.I,
)


def _candidate_blob(facts: list[ResumeFact]) -> tuple[str, set[str], str]:
    parts: list[str] = []
    ids: list[str] = []
    skills: set[str] = set()
    # Only trusted skill tags become matchable skills — not every word in a fact.
    try:
        from apps.resume_generation.services.resume_corpus_service import SKILL_TOKENS
    except Exception:  # pragma: no cover - keep fit usable if import fails
        SKILL_TOKENS = ()  # type: ignore[misc, assignment]
    known = {tok.lower() for tok in SKILL_TOKENS} | {
        "api",
        "backend",
        "cloud",
        "kubernetes",
        "docker",
        "terraform",
        "ansible",
        "kafka",
        "jenkins",
        "pytorch",
        "react",
        "node",
    }
    for fact in facts:
        parts.append(fact.source_text or "")
        ids.append(fact.id)
        for skill in fact.skills_json or []:
            skills.add(skill.lower())
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9+.#-]{1,}", fact.source_text or ""):
            low = token.lower()
            if low in known:
                skills.add(low)
    blob = "\n".join(parts).lower()
    digest = hashlib.sha256("|".join(sorted(ids)).encode()).hexdigest()[:24]
    return blob, skills, digest


_HEADING_ONLY = re.compile(
    r"^(requirements?|qualifications?|preferred|responsibilities|"
    r"about the role|about \w+|key responsibilities|commitment to|"
    r"benefits|what you.?ll do|what we.?re looking for)\b",
    re.I,
)


def _is_location_or_office_line(line: str) -> bool:
    lower = line.lower().strip()
    if _LOCATION_LINE.search(line) and not re.search(
        r"\b(python|java|aws|api|kubernetes|docker|sql|backend|frontend|"
        r"react|terraform|kafka|ci/?cd|microservice)\b",
        lower,
    ):
        return True
    # Short place-only lines: "State College, PA" / "Remote"
    tokens = re.findall(r"[a-zA-Z]+", lower)
    if tokens and all(t in _LOCATION_NOISE or len(t) <= 2 for t in tokens):
        return True
    return False


def _split_long_text(text: str) -> list[str]:
    """Break long JD paragraphs into sentence-sized requirement candidates."""
    chunks: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("•-*–— ").strip()
        if not line:
            continue
        if len(line) <= 220:
            chunks.append(line)
            continue
        for sent in re.split(r"(?<=[.!;])\s+", line):
            cleaned = sent.strip().lstrip("•-*–— ").strip()
            if cleaned:
                chunks.append(cleaned)
    return chunks


def _extract_requirement_lines(description: str) -> list[tuple[str, str]]:
    """Return (line, classification) pairs from the posting text."""
    lines: list[tuple[str, str]] = []
    preferred_section = False
    for line in _split_long_text(description):
        lower = line.lower()
        if any(
            heading in lower
            for heading in (
                "preferred qualification",
                "nice to have",
                "bonus",
                "plus:",
            )
        ) or lower.startswith("preferred"):
            preferred_section = True
            if _HEADING_ONLY.match(lower) or len(line) < 8:
                continue
        if any(
            heading in lower
            for heading in (
                "required qualification",
                "minimum qualification",
                "basic qualification",
                "what you'll need",
            )
        ) or lower in {"requirements", "requirement", "qualifications"}:
            preferred_section = False
            continue
        if _HEADING_ONLY.match(lower) and len(line) < 40:
            continue
        if _is_location_or_office_line(line):
            continue
        if len(line) > 220:
            continue
        if not re.search(r"[a-zA-Z]", line):
            continue
        # Skip soft culture / preference blurbs that are not qualifications.
        if re.search(
            r"\b(someone who likes|passion for|deep interest|best of breed|"
            r"want to be part|we are looking for a)\b",
            lower,
        ):
            continue
        short_skill = len(line) < 12 and bool(
            re.fullmatch(
                r"(python|java|aws|azure|gcp|sql|docker|kubernetes|go|rust|"
                r"c\+\+|typescript|react|node\.?js?)",
                lower,
            )
        )
        if len(line) < 12 and not short_skill:
            continue
        # Prefer lines that look like responsibilities/skills, not marketing blurbs.
        if not short_skill and not re.search(
            r"\b(experience|build|design|develop|own|lead|python|java|aws|"
            r"cloud|api|sql|docker|kubernetes|backend|frontend|data|"
            r"software|engineer|required|responsib|mentor|sql|schema)\b",
            lower,
        ):
            continue
        classification = "preferred" if preferred_section else "required"
        if "preferred" in lower or "nice to have" in lower:
            classification = "preferred"
        lines.append((line, classification))
        if len(lines) >= 14:
            break
    return lines


def _whole_word_in_blob(token: str, blob: str) -> bool:
    """Avoid substring false positives (e.g. 'scala' inside 'microservices')."""
    return (
        re.search(rf"(?<![a-z0-9+.#-]){re.escape(token)}(?![a-z0-9+.#-])", blob, re.I)
        is not None
    )


def _candidate_years_claimed(blob: str) -> int | None:
    """Return the strongest explicit years-of-experience claim in evidence, if any."""
    values = [int(v) for v in re.findall(r"\b(\d+)\s*\+?\s*years?\b", blob, re.I)]
    return max(values) if values else None


def _match_requirement(
    requirement: str, candidate_blob: str, candidate_skills: set[str]
) -> RequirementEvidence:
    tokens = []
    for raw in re.findall(r"[a-zA-Z][a-zA-Z0-9+.#-]{2,}", requirement):
        # Trailing '.' from sentence ends must not create distinct tokens
        # (e.g. "concepts." bypassing the "concepts" filler entry).
        t = raw.lower().rstrip(".")
        if len(t) < 3 or t in _MATCH_STOP:
            continue
        tokens.append(t)
    hits = [
        t
        for t in tokens
        if t in candidate_skills or _whole_word_in_blob(t, candidate_blob)
    ]
    filler = GENERIC_ACTION_FILLER | _LOCATION_NOISE
    # Inventory skill tags that also appear in the requirement.
    tech_hits = [t for t in hits if t in candidate_skills]
    # Non-filler content (may include tech names and domain nouns).
    non_filler = [t for t in hits if t not in filler]
    # "backend"/"software" skill tags are too generic to prove a specialty alone.
    substantive_tech = [t for t in tech_hits if t not in filler]
    compact_requirement = len(tokens) <= 6
    years_needed = re.search(r"\b(\d+)\s*\+?\s*years?\b", requirement, re.I)
    if years_needed:
        needed = int(years_needed.group(1))
        claimed = _candidate_years_claimed(candidate_blob)
        if claimed is None or claimed < needed:
            status = STATUS_ADJACENT if (substantive_tech or non_filler) else STATUS_NOT_DEMONSTRATED
            explanation = (
                f"Posting asks for {needed}+ years; verified evidence does not "
                "state a matching years-of-experience claim."
            )
            importance = "high"
            if any(x in requirement.lower() for x in ("preferred", "nice to have", "bonus")):
                importance = "low"
            return RequirementEvidence(
                requirement=requirement[:180],
                classification="preferred" if importance == "low" else "required",
                supporting_fact_ids=[],
                evidence_status=status,
                importance=importance,
                explanation=explanation,
            )
    if re.search(r"\bmentor", requirement, re.I) and not re.search(
        r"\bmentor", candidate_blob, re.I
    ):
        status = STATUS_NOT_DEMONSTRATED
        explanation = (
            "Mentorship is required, but mentoring is not demonstrated in "
            "verified evidence."
        )
    elif substantive_tech and len(non_filler) >= 1:
        status = STATUS_DEMONSTRATED
        shown = list(dict.fromkeys([*substantive_tech, *non_filler]))[:4]
        explanation = f"Verified inventory supports this via: {', '.join(shown)}."
    elif substantive_tech and compact_requirement:
        # Short skill lines ("Python", "AWS", "Docker and Kubernetes").
        status = STATUS_DEMONSTRATED
        explanation = (
            f"Verified inventory includes required skill(s): "
            f"{', '.join(substantive_tech[:4])}."
        )
    elif tech_hits:
        status = STATUS_ADJACENT
        explanation = (
            f"Only thin skill overlap ({', '.join(tech_hits[:3])}); "
            "generic wording like design/lead is not treated as core proof."
        )
    elif non_filler and len(non_filler) >= 2:
        status = STATUS_ADJACENT
        explanation = (
            f"Transferable wording overlap ({', '.join(non_filler[:3])}); "
            "specialty tools not clearly demonstrated."
        )
    elif hits:
        status = STATUS_ADJACENT
        explanation = (
            f"Only generic overlap ({', '.join(hits[:3])}); specialty not demonstrated."
        )
    else:
        status = STATUS_NOT_DEMONSTRATED
        explanation = "No matching verified candidate evidence for this requirement."
    importance = "high"
    if any(x in requirement.lower() for x in ("preferred", "nice to have", "bonus")):
        importance = "low"
    elif status == STATUS_ADJACENT:
        importance = "medium"
    # Mentorship / senior lead language is high importance when required.
    if re.search(r"\b(mentor|staff|principal|lead the design|10\+|8\+)\b", requirement, re.I):
        if importance != "low":
            importance = "high"
    return RequirementEvidence(
        requirement=requirement[:180],
        classification="preferred" if importance == "low" else "required",
        supporting_fact_ids=[],
        evidence_status=status,
        importance=importance,
        explanation=explanation,
    )


_TITLE_HARDWARE = re.compile(
    r"\b(asic|fpga|rtl|verilog|systemverilog|vhdl|soc verification|"
    r"formal verification|digital design|hardware verification)\b",
    re.I,
)


def detect_domain_mismatch(
    title: str, description: str, candidate_blob: str
) -> tuple[bool, str | None]:
    """Decisive specialist-domain mismatch from responsibilities, not company/title alone."""
    blob = f"{title}\n{description}".lower()
    title_l = title.lower()
    # Title-only specialist roles (common on stub listings) still count when the
    # candidate inventory lacks corresponding hardware evidence.
    if _TITLE_HARDWARE.search(title_l) and not re.search(
        r"\b(software|tooling|compiler|backend|platform)\b", title_l
    ):
        if not re.search(
            r"\b(rtl|verilog|systemverilog|fpga|asic|vhdl|uvm|hdl)\b",
            candidate_blob,
            flags=re.I,
        ):
            return True, (
                "Low fit: core hardware / verification title requirements are not "
                "demonstrated in verified evidence."
            )
    for _key, profile in DOMAIN_PROFILES.items():
        core_hits = [
            pat for pat in profile["core"] if re.search(pat, blob, flags=re.I)
        ]
        if len(core_hits) < int(profile["min_core_hits"]):
            continue
        softwareish = sum(
            1 for pat in profile["software_context"] if re.search(pat, blob, flags=re.I)
        )
        if softwareish >= 2:
            # Likely software adjacent to the domain — do not hard-mismatch.
            continue
        candidate_has = sum(
            1 for pat in profile["core"] if re.search(pat, candidate_blob, flags=re.I)
        )
        if candidate_has >= 2:
            continue
        label = profile["label"]
        return True, (
            f"Low fit: core {label} requirements are not demonstrated in verified evidence."
        )
    return False, None


def _seniority_gap(title: str, description: str, candidate_blob: str) -> bool:
    blob = f"{title}\n{description}".lower()
    if not any(re.search(pat, blob, flags=re.I) for pat in SENIORITY_MARKERS):
        # Explicit mentorship as a required operating mode still counts.
        if not re.search(r"\bmentor(ing|ship)?\b", blob, flags=re.I):
            return False
    # Early-career candidate inventory typically lacks long ownership language.
    ownership = len(
        re.findall(r"\b(led|owned|architected|managed team|roadmap|mentored)\b", candidate_blob)
    )
    senior_title = bool(re.search(r"\b(10\+|8\+|staff|principal|iii|sr\.?)\b", blob, flags=re.I))
    mentor_required = bool(re.search(r"\bmentor(ing|ship)?\b", blob, flags=re.I))
    if ownership > 0:
        # Mentorship asked but never demonstrated remains a gap even with "led".
        if mentor_required and not re.search(r"\bmentor", candidate_blob, flags=re.I):
            return True
        return False
    return senior_title or mentor_required


def band_for_score(
    score: int,
    *,
    domain_mismatch: bool,
    confidence: str,
    recommended_min: int | None = None,
    stretch_min: int | None = None,
) -> str:
    if confidence == CONFIDENCE_INSUFFICIENT:
        return BAND_NEEDS_REVIEW
    if domain_mismatch:
        return BAND_LOW_FIT
    rec = (
        recommended_min
        if recommended_min is not None
        else settings.fit_recommended_min
    )
    stretch = stretch_min if stretch_min is not None else settings.fit_stretch_min
    if score >= rec:
        return BAND_RECOMMENDED
    if score >= stretch:
        return BAND_STRETCH
    return BAND_LOW_FIT


def assess_evidence_fit(
    job: JobPosting,
    facts: list[ResumeFact],
    *,
    now: datetime | None = None,
) -> EvidenceFitAssessment:
    """Score 0–100 from verified candidate evidence vs posting requirements.

    This is not the queue ranking priority score and not a hiring probability.
    """
    description = job.description_text or ""
    title = job.title or ""
    clock = now or datetime.now(UTC)
    candidate_blob, candidate_skills, evidence_hash = _candidate_blob(facts)
    jd_hash = hash_text(description)

    mismatch, mismatch_reason = detect_domain_mismatch(
        title, description, candidate_blob
    )
    if mismatch:
        return EvidenceFitAssessment(
            band=BAND_LOW_FIT,
            score=20,
            confidence=CONFIDENCE_MEDIUM if len(description.strip()) >= 120 else CONFIDENCE_LOW,
            short_reason=mismatch_reason
            or "Low fit: core domain requirements are not demonstrated.",
            domain_mismatch=True,
            domain_mismatch_reason=mismatch_reason,
            job_description_hash=jd_hash,
            candidate_evidence_hash=evidence_hash,
            assessed_at=clock.isoformat(),
            eligible_for_default_feed=False,
        )

    if not facts:
        return EvidenceFitAssessment(
            band=BAND_NEEDS_REVIEW,
            score=0,
            confidence=CONFIDENCE_INSUFFICIENT,
            short_reason="Needs review: no verified candidate evidence is indexed yet.",
            domain_mismatch=False,
            job_description_hash=jd_hash,
            candidate_evidence_hash=evidence_hash,
            assessed_at=clock.isoformat(),
            eligible_for_default_feed=False,
        )
    # Stub listings (title/location only) cannot support a confident Recommended score.
    if len(description.strip()) < 250:
        return EvidenceFitAssessment(
            band=BAND_NEEDS_REVIEW,
            score=0,
            confidence=CONFIDENCE_INSUFFICIENT,
            short_reason=(
                "Needs review: posting lacks enough requirements for reliable scoring."
            ),
            domain_mismatch=False,
            job_description_hash=jd_hash,
            candidate_evidence_hash=evidence_hash,
            assessed_at=clock.isoformat(),
            eligible_for_default_feed=False,
        )
    req_lines = _extract_requirement_lines(description)
    if not req_lines:
        # Fall back to known technical tokens present in the description.
        from apps.resume_generation.services.resume_corpus_service import SKILL_TOKENS

        described = description.lower()
        uniq: list[str] = []
        for tok in (*SKILL_TOKENS, "api", "backend", "cloud", "kubernetes"):
            if tok in described and tok not in uniq:
                uniq.append(tok)
            if len(uniq) >= 8:
                break
        req_lines = [(tok, "required") for tok in uniq]

    requirements = [
        _match_requirement(line, candidate_blob, candidate_skills)
        for line, _cls in req_lines
    ]
    for req, (_line, classification) in zip(requirements, req_lines, strict=False):
        req.classification = classification
        if classification == "preferred":
            req.importance = "low"

    required = [r for r in requirements if r.classification != "preferred"]
    preferred = [r for r in requirements if r.classification == "preferred"]
    if not required:
        required = requirements

    demonstrated = sum(
        1 for r in required if r.evidence_status == STATUS_DEMONSTRATED
    )
    adjacent = sum(1 for r in required if r.evidence_status == STATUS_ADJACENT)
    missing_required = sum(
        1 for r in required if r.evidence_status == STATUS_NOT_DEMONSTRATED
    )
    preferred_hits = sum(
        1 for r in preferred if r.evidence_status == STATUS_DEMONSTRATED
    )

    base = 35
    if required:
        base = int(100 * (demonstrated + 0.45 * adjacent) / max(1, len(required)))
    # Preferred skills are secondary only.
    base = min(100, base + min(8, preferred_hits * 2))
    # Generic-only adjacent matches should not look strong.
    if demonstrated == 0 and adjacent > 0:
        base = min(base, 48)
    if _seniority_gap(title, description, candidate_blob):
        base = min(base, 55)
        requirements.append(
            RequirementEvidence(
                requirement="Senior ownership / long-horizon scope",
                classification="required",
                evidence_status=STATUS_NOT_DEMONSTRATED,
                importance="high",
                explanation=(
                    "Posting asks for senior ownership scope not demonstrated "
                    "in verified inventory."
                ),
            )
        )

    if mismatch:
        # Core-domain mismatch cannot be averaged away by Python/Linux/C++ overlap.
        base = min(base, 28)

    confidence = CONFIDENCE_MEDIUM
    if len(required) >= 4 and demonstrated + adjacent >= 2:
        confidence = CONFIDENCE_HIGH
    if len(description.strip()) < 250 or len(required) < 2:
        confidence = CONFIDENCE_LOW

    band = band_for_score(base, domain_mismatch=mismatch, confidence=confidence)

    if mismatch and mismatch_reason:
        short = mismatch_reason
    elif band == BAND_RECOMMENDED:
        gap = missing_required
        if gap == 0:
            short = "Strong match to demonstrated backend/platform evidence."
        else:
            short = (
                f"Strong demonstrated match; {gap} required item(s) still not shown."
            )
    elif band == BAND_STRETCH:
        short = (
            "Stretch: relevant transferable evidence, but some required "
            "capabilities are only adjacent or missing."
        )
    else:
        short = (
            "Low fit: too many required capabilities are not demonstrated "
            "in verified evidence."
        )

    return EvidenceFitAssessment(
        band=band,
        score=max(0, min(100, base)),
        confidence=confidence,
        short_reason=short,
        domain_mismatch=mismatch,
        domain_mismatch_reason=mismatch_reason,
        requirements=requirements[:12],
        job_description_hash=jd_hash,
        candidate_evidence_hash=evidence_hash,
        assessed_at=clock.isoformat(),
        eligible_for_default_feed=band == BAND_RECOMMENDED,
    )


def get_stored_fit(job: JobPosting) -> dict[str, Any] | None:
    meta = job.intake_metadata or {}
    block = meta.get("evidence_fit")
    return block if isinstance(block, dict) else None


def store_fit_assessment(job: JobPosting, assessment: EvidenceFitAssessment) -> None:
    meta = dict(job.intake_metadata or {})
    meta["evidence_fit"] = assessment.to_dict()
    job.intake_metadata = meta


def assess_and_store(session: Session, job: JobPosting) -> EvidenceFitAssessment:
    from apps.resume_generation.services.resume_fact_service import usable_facts

    facts = usable_facts(session)
    assessment = assess_evidence_fit(job, facts)
    store_fit_assessment(job, assessment)
    session.add(job)
    return assessment


def reassess_jobs(
    session: Session,
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    from apps.resume_generation.services.resume_fact_service import usable_facts

    facts = usable_facts(session)
    q = session.query(JobPosting).order_by(JobPosting.created_at.desc())
    if limit is not None:
        q = q.limit(limit)
    jobs = q.all()
    counts = {
        BAND_RECOMMENDED: 0,
        BAND_STRETCH: 0,
        BAND_LOW_FIT: 0,
        BAND_NEEDS_REVIEW: 0,
    }
    for job in jobs:
        assessment = assess_evidence_fit(job, facts)
        counts[assessment.band] = counts.get(assessment.band, 0) + 1
        if not dry_run:
            store_fit_assessment(job, assessment)
            session.add(job)
    if not dry_run:
        session.flush()
    return {
        "scanned": len(jobs),
        "dry_run": dry_run,
        "policy_version": FIT_POLICY_VERSION,
        "counts": counts,
        "candidate_facts": len(facts),
    }
