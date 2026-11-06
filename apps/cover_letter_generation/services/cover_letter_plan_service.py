from __future__ import annotations

import re
from collections.abc import Callable

from sqlalchemy.orm import Session

from apps.cover_letter_generation.paths import (
    COMPANY_EVIDENCE_UNAVAILABLE,
    DEFAULT_VOICE,
    NO_EVIDENCE_MSG,
    VOICE_MODES,
)
from apps.cover_letter_generation.schemas import CoverLetterPlan, StoryEvidence
from apps.cover_letter_generation.services.company_research_service import (
    Transport,
    research_company,
)
from apps.cover_letter_generation.services.cover_letter_preflight_service import (
    cover_letter_preflight,
)
from apps.resume_generation.schemas import ExperienceEntry, ProjectEntry, ResumeDocument
from apps.resume_generation.services.resume_corpus_service import SKILL_TOKENS
from apps.resume_generation.services.job_requirement_service import (
    extract_job_requirements,
)
from apps.resume_generation.services.structured_document_service import (
    build_resume_document,
)
from packages.core.domain.models import JobPosting, ResumeGeneration

_METRIC_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|percent|hours?)\b", re.I)


def _normalize_voice(voice: str | None) -> str:
    chosen = (voice or DEFAULT_VOICE).strip().lower()
    if chosen not in VOICE_MODES:
        return DEFAULT_VOICE
    return chosen


def _metric(bullets: list[str]) -> str | None:
    for bullet in bullets:
        match = _METRIC_RE.search(bullet)
        if match:
            return match.group(0)
    return None


_TERM_STOP = {
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
    "job",
    "role",
    "team",
    "work",
    "experience",
    "using",
    "ability",
    "software",
    "developer",
    "engineer",
    "end",
    "time",
    "consumer",
    "hour",
    "hours",
    "using",
    "across",
    "within",
    "their",
    "about",
    "would",
    "which",
    "where",
    "there",
    "other",
    "production",
    "backend",
    "workflows",
    "workflow",
}


def _terms(text: str, keywords: list[str], description: str = "") -> list[str]:
    low = text.lower()
    described = description.lower()
    hits: list[str] = []
    skill_order = [token for token in keywords if token in SKILL_TOKENS or token in {"api", "cloud"}]
    for skill in (*skill_order, *SKILL_TOKENS, "api", "cloud"):
        if skill in low and skill in described and skill not in hits:
            hits.append(skill)
        if len(hits) >= 4:
            break
    return hits[:4]


def _from_experience(
    entry: ExperienceEntry, keywords: list[str], description: str
) -> StoryEvidence | None:
    bullets = [bullet.text.strip() for bullet in entry.bullets if bullet.text.strip()]
    if not entry.company or not bullets:
        return None
    blob = " ".join(bullets)
    terms = _terms(blob, keywords, description)
    employer = " ".join(entry.company.split())
    title = " ".join((entry.title or "engineer").split())
    return StoryEvidence(
        kind="experience",
        label=f"{title} at {employer}",
        employer=employer,
        title=title,
        fact_ids=[bullet.fact_id for bullet in entry.bullets],
        source_bullets=bullets,
        overlap_terms=terms,
        metric=_metric(bullets),
    )


def _from_project(
    entry: ProjectEntry, keywords: list[str], description: str
) -> StoryEvidence | None:
    bullets = [bullet.text.strip() for bullet in entry.bullets if bullet.text.strip()]
    if not entry.name or not bullets:
        return None
    blob = " ".join(bullets)
    name = " ".join(entry.name.split())
    return StoryEvidence(
        kind="project",
        label=name,
        employer=name,
        title="project",
        fact_ids=[bullet.fact_id for bullet in entry.bullets],
        source_bullets=bullets,
        overlap_terms=_terms(blob, keywords, description) or [entry.name],
        metric=_metric(bullets),
    )


def _score(story: StoryEvidence) -> int:
    base = len(story.overlap_terms) * 10 + (5 if story.metric else 0)
    # Prefer verified employment over projects when overlap is otherwise similar.
    if story.kind == "experience":
        base += 4
    if story.metric:
        base += 2
    return base


def select_stories(
    document: ResumeDocument, keywords: list[str], description: str = ""
) -> tuple[StoryEvidence | None, StoryEvidence | None, list[str]]:
    stories: list[StoryEvidence] = []
    for experience in document.experience:
        story = _from_experience(experience, keywords, description)
        if story:
            stories.append(story)
    for project in document.projects:
        project_story = _from_project(project, keywords, description)
        if project_story:
            stories.append(project_story)
    stories.sort(key=_score, reverse=True)
    if not stories:
        return None, None, []
    primary = stories[0]
    secondary = next(
        (story for story in stories[1:] if story.label != primary.label),
        None,
    )
    used = set(primary.fact_ids)
    if secondary:
        used.update(secondary.fact_ids)
    omitted = [fact_id for fact_id in document.fact_ids if fact_id not in used]
    return primary, secondary, omitted


def _resume_context(session: Session, job_id: str) -> tuple[str | None, bool]:
    row = (
        session.query(ResumeGeneration)
        .filter(
            ResumeGeneration.job_posting_id == job_id,
            ResumeGeneration.status == "succeeded",
            ResumeGeneration.quality_status == "active",
        )
        .order_by(ResumeGeneration.created_at.desc())
        .first()
    )
    if row is None or not row.selected_track:
        return None, False
    return row.selected_track, True


def build_cover_letter_plan(
    session: Session,
    job: JobPosting,
    *,
    voice: str | None = None,
    skip_company_research: bool = False,
    transport: Transport | None = None,
    document_loader: Callable[..., ResumeDocument] | None = None,
) -> CoverLetterPlan:
    chosen_voice = _normalize_voice(voice)
    company = job.company.canonical_name if job.company else ""
    block = cover_letter_preflight(session, job)
    if block:
        return CoverLetterPlan(
            job_id=job.id,
            company=company,
            role=job.title or "",
            voice=chosen_voice,
            role_family="general",
            resume_track=None,
            used_resume_generation=False,
            contact_line=None,
            primary=None,
            secondary=None,
            block_reason=block,
            warnings=[block],
        )

    requirements = extract_job_requirements(job)
    track, used_resume = _resume_context(session, job.id)
    if not track:
        track = requirements.role_family
        used_resume = False
    loader = document_loader or build_resume_document
    document = loader(session, track=track, keywords=requirements.keywords)
    description = f"{job.title}\n{job.description_text or ''}"
    primary, secondary, omitted = select_stories(
        document, requirements.keywords, description
    )
    warnings: list[str] = []
    if primary is None:
        warnings.append(NO_EVIDENCE_MSG)
        return CoverLetterPlan(
            job_id=job.id,
            company=company,
            role=job.title,
            voice=chosen_voice,
            role_family=requirements.role_family,
            resume_track=track,
            used_resume_generation=used_resume,
            contact_line=document.contact_line,
            primary=None,
            secondary=None,
            warnings=warnings,
            omitted_fact_ids=omitted,
            block_reason=NO_EVIDENCE_MSG,
        )

    domain = job.company.website_domain if job.company else None
    evidence, research_warnings = research_company(
        company_name=company,
        website_domain=domain,
        apply_url=job.company_apply_url,
        transport=transport,
        skip=skip_company_research,
    )
    warnings.extend(research_warnings)
    if not evidence and COMPANY_EVIDENCE_UNAVAILABLE not in warnings:
        warnings.append(COMPANY_EVIDENCE_UNAVAILABLE)
    if chosen_voice == "mission_oriented" and not evidence:
        warnings.append(
            "Mission voice requested, but no official company evidence was available; "
            "the letter stays with verified candidate evidence."
        )

    outline = [
        f"Open on the {job.title} role at {company} and one factual reason.",
        f"Primary example: {primary.label}.",
        (
            f"Secondary example: {secondary.label}."
            if secondary
            else "Close by connecting the primary example to the role."
        ),
    ]
    return CoverLetterPlan(
        job_id=job.id,
        company=company,
        role=job.title,
        voice=chosen_voice,
        role_family=requirements.role_family,
        resume_track=track,
        used_resume_generation=used_resume,
        contact_line=document.contact_line,
        primary=primary,
        secondary=secondary,
        company_evidence=evidence,
        outline=outline,
        warnings=warnings,
        omitted_fact_ids=omitted,
    )
