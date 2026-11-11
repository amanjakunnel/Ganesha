"""Load approved fillable profile fields without LLM calls or sensitive answers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from apps.cover_letter_generation.paths import CANDIDATE_NAME
from packages.core.domain.models import ResumeFact

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(
    r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
)
_LINKEDIN = re.compile(r"https?://(?:www\.)?linkedin\.com/[^\s|]+", re.IGNORECASE)
_GITHUB = re.compile(r"https?://(?:www\.)?github\.com/[^\s|]+", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s|]+", re.IGNORECASE)


@dataclass(frozen=True)
class ApprovedProfile:
    full_name: str
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    # Education — verified corpus facts only (never invented).
    school: str | None = None
    degree: str | None = None
    discipline: str | None = None
    education_start: str | None = None
    education_end: str | None = None


def _split_name(full: str) -> tuple[str, str]:
    parts = [p for p in (full or "").strip().split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def load_approved_profile(session: Session) -> ApprovedProfile:
    """Best-effort approved contact fields from corpus facts + configured name."""
    facts = (
        session.query(ResumeFact)
        .filter(ResumeFact.usable.is_(True))
        .order_by(ResumeFact.created_at.asc())
        .limit(400)
        .all()
    )
    blob = "\n".join((f.source_text or "") for f in facts)
    email = _EMAIL.search(blob)
    phone = _PHONE.search(blob)
    linkedin = _LINKEDIN.search(blob)
    github = _GITHUB.search(blob)
    portfolio = None
    for match in _URL.finditer(blob):
        url = match.group(0).rstrip(".,)")
        if "linkedin.com" in url.lower() or "github.com" in url.lower():
            continue
        portfolio = url
        break
    location = None
    for fact in facts:
        text = (fact.source_text or "").strip()
        if not text or "@" in text:
            continue
        if re.search(r"\b(PA|NY|CA|TX|WA|MA|IL|State College)\b", text):
            # Prefer short contact-style location lines.
            if len(text) < 80 and not text.lower().startswith("http"):
                location = text.split("|")[0].strip()
                break
    first, last = _split_name(CANDIDATE_NAME)
    school, degree, discipline, edu_start, edu_end = _extract_education(facts)
    return ApprovedProfile(
        full_name=CANDIDATE_NAME,
        first_name=first,
        last_name=last,
        email=email.group(0) if email else None,
        phone=phone.group(0) if phone else None,
        location=location,
        linkedin_url=linkedin.group(0).rstrip(".,)") if linkedin else None,
        github_url=github.group(0).rstrip(".,)") if github else None,
        portfolio_url=portfolio,
        school=school,
        degree=degree,
        discipline=discipline,
        education_start=edu_start,
        education_end=edu_end,
    )


def _extract_education(
    facts: list[ResumeFact],
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    school = degree = discipline = edu_start = edu_end = None
    for fact in facts:
        text = (fact.source_text or "").strip()
        if not text:
            continue
        low = text.lower()
        if school is None and any(
            k in low
            for k in (
                "pennsylvania state",
                "penn state",
                "pes university",
                "university",
                "college",
            )
        ):
            if len(text) < 120:
                school = text.split("|")[0].strip()
        if degree is None and any(
            k in low for k in ("bachelor", "master", "b.s", "b.s.", "m.s", "bs ", "ms ")
        ):
            degree = text.split("|")[0].strip()[:80]
        if discipline is None and any(
            k in low
            for k in (
                "computer science",
                "software",
                "information science",
                "engineering",
            )
        ):
            if len(text) < 100:
                discipline = text.split("|")[0].strip()
        years = re.findall(r"\b(20\d{2})\b", text)
        if years and edu_start is None:
            edu_start = years[0]
            if len(years) > 1:
                edu_end = years[-1]
    return school, degree, discipline, edu_start, edu_end
