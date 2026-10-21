from __future__ import annotations

from dataclasses import dataclass, field, replace

from sqlalchemy.orm import Session

from apps.resume_generation.schemas import ResumeStrategy
from packages.core.domain.models import ResumeFact


@dataclass
class ComposedResume:
    name_line: str
    contact_line: str | None
    summary: str | None
    skills: list[str] = field(default_factory=list)
    experience: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)
    omit_reasons: list[str] = field(default_factory=list)


def _looks_like_contact(text: str) -> bool:
    lower = text.lower()
    return any(
        token in lower
        for token in ("@", "linkedin.com", "github.com", "tel:", "+", "http")
    ) or (
        any(ch.isdigit() for ch in text)
        and ("|" in text or "•" in text or "," in text)
        and len(text) < 160
    )


def _is_sectionish_header(text: str) -> bool:
    """Skip short role/employer headers that are not achievement bullets."""
    if len(text) > 140:
        return False
    if text.endswith("."):
        return False
    words = text.split()
    if len(words) <= 10 and any(
        token in text.lower()
        for token in ("jan ", "feb ", "mar ", "apr ", "may ", "jun ", "jul ", "aug ", "sep ", "oct ", "nov ", "dec ", "present")
    ):
        return True
    return False


def compose_resume(
    session: Session,
    strategy: ResumeStrategy,
    *,
    job_title: str,
    include_summary: bool = False,
) -> ComposedResume:
    facts_by_id = {
        f.id: f
        for f in session.query(ResumeFact)
        .filter(ResumeFact.id.in_(strategy.selected_fact_ids or ["__none__"]))
        .all()
    }
    ordered: list[ResumeFact] = []
    for fid in strategy.selected_fact_ids:
        fact = facts_by_id.get(fid)
        if fact and fact.usable:
            ordered.append(fact)

    name_line = "Alex Michael Manjakunnel"
    contact_line: str | None = None
    skills: list[str] = []
    experience: list[str] = []
    projects: list[str] = []
    education: list[str] = []
    used_ids: list[str] = []
    omit_reasons: list[str] = []

    for fact in ordered:
        text = fact.source_text.strip()
        if not text:
            continue
        if fact.section == "other" and _looks_like_contact(text) and not contact_line:
            contact_line = text.replace("•", "|")
            used_ids.append(fact.id)
            continue
        if fact.section == "skills":
            if text not in skills and len(skills) < 4:
                skills.append(text)
                used_ids.append(fact.id)
        elif fact.section == "experience":
            if _is_sectionish_header(text):
                # Keep a compact employer/role line, capped
                if text not in experience and len(experience) < 8:
                    experience.append(text)
                    used_ids.append(fact.id)
                continue
            if text not in experience and len(experience) < 8:
                experience.append(text)
                used_ids.append(fact.id)
        elif fact.section == "projects":
            if text not in projects and len(projects) < 3:
                projects.append(text)
                used_ids.append(fact.id)
        elif fact.section == "education":
            if text not in education and len(education) < 3:
                education.append(text)
                used_ids.append(fact.id)

    if len(experience) + len(projects) > 9:
        dropped = projects[2:]
        projects = projects[:2]
        if dropped:
            omit_reasons.append(
                f"Omitted {len(dropped)} project line(s) for one-page budget."
            )

    summary = None
    if include_summary and strategy.track and job_title:
        summary = (
            f"Software engineer targeting {job_title} roles with verified "
            f"{strategy.track} experience drawn from existing resume evidence."
        )

    return ComposedResume(
        name_line=name_line,
        contact_line=contact_line,
        summary=summary,
        skills=skills,
        experience=experience,
        projects=projects,
        education=education,
        fact_ids=used_ids,
        omit_reasons=omit_reasons,
    )


def trim_for_page_budget(composed: ComposedResume, *, pass_number: int) -> ComposedResume:
    """Drop lowest-priority content; record why. Does not invent replacements."""
    reasons = list(composed.omit_reasons)
    skills = list(composed.skills)
    experience = list(composed.experience)
    projects = list(composed.projects)
    education = list(composed.education)
    summary = composed.summary

    if pass_number == 1:
        if summary:
            reasons.append("Dropped summary to fit one page.")
            summary = None
        if len(projects) > 1:
            reasons.append(f"Trimmed projects from {len(projects)} to 1 for page budget.")
            projects = projects[:1]
        if len(experience) > 6:
            reasons.append(
                f"Trimmed experience from {len(experience)} to 6 for page budget."
            )
            experience = experience[:6]
    elif pass_number >= 2:
        if projects:
            reasons.append("Dropped remaining projects to fit one page.")
            projects = []
        if len(skills) > 2:
            reasons.append(f"Trimmed skills from {len(skills)} to 2 for page budget.")
            skills = skills[:2]
        if len(experience) > 4:
            reasons.append(
                f"Trimmed experience from {len(experience)} to 4 for page budget."
            )
            experience = experience[:4]
        if len(education) > 2:
            reasons.append(
                f"Trimmed education from {len(education)} to 2 for page budget."
            )
            education = education[:2]

    return replace(
        composed,
        summary=summary,
        skills=skills,
        experience=experience,
        projects=projects,
        education=education,
        omit_reasons=reasons,
    )
