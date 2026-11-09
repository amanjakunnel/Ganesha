from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from apps.application_execution.paths import SENSITIVE_ANSWER_CATEGORIES
from apps.cover_letter_generation.paths import CANDIDATE_NAME
from packages.core.domain.models import ResumeFact


def build_profile_data_ref(session: Session) -> dict[str, object]:
    """Reference approved profile field availability without copying PII into the package."""
    facts = (
        session.query(ResumeFact)
        .filter(ResumeFact.usable.is_(True))
        .order_by(ResumeFact.created_at.asc())
        .limit(500)
        .all()
    )
    digest = hashlib.sha256()
    digest.update(CANDIDATE_NAME.encode())
    for fact in facts:
        digest.update((fact.content_hash or fact.id).encode())
    sections = {str(f.section or "").lower() for f in facts}
    available: list[str] = ["name"]
    # Contact is derived from structured resume composition when facts exist.
    if facts:
        available.extend(["email", "phone", "location"])
    if any("education" in s for s in sections):
        available.append("education")
    if any(s in {"experience", "employment", "work"} for s in sections):
        available.append("employment_history")
    # Portfolio links are only marked available when explicitly present in facts.
    blob = " ".join((f.source_text or "").lower() for f in facts)
    if "linkedin.com" in blob or "linkedin" in blob:
        available.append("linkedin")
    if "github.com" in blob or "github" in blob:
        available.append("github")
    if "http://" in blob or "https://" in blob:
        available.append("portfolio")
    return {
        "source": "resume_corpus",
        "candidate_name_configured": bool(CANDIDATE_NAME.strip()),
        "version": digest.hexdigest()[:24],
        "fields_available": available,
        "fields_requiring_explicit_user_answer": sorted(SENSITIVE_ANSWER_CATEGORIES),
        "note": (
            "Sensitive employer questions are never answered from eligibility "
            "decisions or inferred defaults."
        ),
    }


def sensitive_question_reminders() -> list[str]:
    return [
        "Work authorization, sponsorship, citizenship, and clearance answers require "
        "your explicit input on the employer form.",
        "Salary, relocation, start date, demographic, disability, legal, and consent "
        "questions are never auto-filled by Ganesha.",
        "A prior job-eligibility decision is not an approved answer to differently "
        "worded employer questions.",
    ]
