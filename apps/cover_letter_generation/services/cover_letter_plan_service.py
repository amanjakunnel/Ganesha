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

# Require a before→after relationship in the *same* bullet. Orphan figures
# (e.g. "3.5 hours" alone) must not become letter claims.
_METRIC_PAIR_RE = re.compile(
    r"from\s+(?:roughly\s+|about\s+|~)?(?P<before>\d+(?:\.\d+)?\s*(?:hours?|%|percent))"
    r"\s+to\s+(?:under\s+|about\s+|roughly\s+|~)?(?P<after>\d+(?:\.\d+)?\s*(?:hours?|%|percent))",
    re.I,
)


def _normalize_voice(voice: str | None) -> str:
    chosen = (voice or DEFAULT_VOICE).strip().lower()
    if chosen not in VOICE_MODES:
        return DEFAULT_VOICE
    return chosen


def _primary_bullet(bullets: list[str]) -> str | None:
    """Bullet used for narration — first substantial line."""
    for bullet in bullets:
        cleaned = " ".join(bullet.split()).strip()
        if len(cleaned) >= 24:
            return cleaned
    return " ".join(bullets[0].split()).strip() if bullets else None


def _complete_metric_from_bullet(bullet: str | None) -> str | None:
    """Return a before/after metric only when the selected bullet states both ends."""
    if not bullet:
        return None
    match = _METRIC_PAIR_RE.search(bullet)
    if not match:
        return None
    before = " ".join(match.group("before").split())
    after = " ".join(match.group("after").split())
    return f"from {before} to {after}"


def _metric_for_story(bullets: list[str]) -> str | None:
    """Attach a metric only to the narrated bullet — never a sibling accomplishment."""
    return _complete_metric_from_bullet(_primary_bullet(bullets))


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


_RESP_STOP = {
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
    "using",
    "ability",
    "experience",
    "including",
    "through",
    "their",
    "your",
    "into",
    "such",
    "than",
    "also",
    "over",
}


def _content_tokens(text: str) -> set[str]:
    # Keep technical nouns (backend, cloud, production) — they are useful for
    # responsibility matching even when too generic for skill dumps.
    return {
        tok
        for tok in re.findall(r"[a-zA-Z][a-zA-Z0-9+.#-]{2,}", text.lower())
        if tok not in _RESP_STOP
    }


def _extract_jd_responsibilities(description: str) -> list[str]:
    """Pull short responsibility phrases from the posting for letter narration."""
    lines: list[str] = []
    for raw in (description or "").splitlines():
        line = raw.strip().lstrip("•-*–— ").strip()
        if not line:
            continue
        if len(line) > 220:
            for sent in re.split(r"(?<=[.!;])\s+", line):
                cleaned = sent.strip().lstrip("•-*–— ").strip()
                if cleaned:
                    lines.append(cleaned)
            continue
        lines.append(line)
    responsibilities: list[str] = []
    for line in lines:
        lower = line.lower()
        if re.search(
            r"\b(we are open to hiring|headquarters|remote|hybrid|benefits|"
            r"equal opportunity|about the company|someone who likes|"
            r"passion for|deep interest|want to be part)\b",
            lower,
        ):
            continue
        if not re.search(
            r"\b(lead|own|design|develop|build|implement|maintain|mentor|"
            r"troubleshoot|collaborate|deliver|process|deploy|monitor)\b",
            lower,
        ):
            continue
        # Prefer imperative responsibility lines over soft prose.
        if not re.match(
            r"^(lead|own|design|develop|build|implement|maintain|mentor|"
            r"troubleshoot|collaborate|deliver|deploy|monitor|apply|"
            r"investigate|conduct|define)\b",
            lower,
        ):
            continue
        if len(line) < 28 or len(line) > 180:
            continue
        responsibilities.append(" ".join(line.split()))
        if len(responsibilities) >= 10:
            break
    return responsibilities


def _attach_responsibility(
    story: StoryEvidence, responsibilities: list[str]
) -> StoryEvidence:
    blob_tokens = _content_tokens(" ".join(story.source_bullets))
    skill_tokens = {term.lower() for term in story.overlap_terms}
    best: str | None = None
    best_score = 0
    for resp in responsibilities:
        resp_tokens = _content_tokens(resp)
        shared = blob_tokens & resp_tokens
        # Prefer shared technical/action substance over single generic words.
        score = len(shared) + 3 * len(shared & skill_tokens)
        if score > best_score:
            best_score = score
            best = resp
    if best is None and responsibilities:
        best = responsibilities[0]
        best_score = 0
    connection = "strong" if best_score >= 2 else "weak"
    # Shorten for prose without sounding like a keyword dump.
    responsibility = None
    if best and best_score > 0:
        words = best.split()
        # Keep the ask readable in a letter; cut at a natural boundary.
        responsibility = " ".join(words[:14]).rstrip(",;:")
        for sep in (", ", "; ", " including ", " that ", " with "):
            idx = responsibility.rfind(sep)
            if idx >= 40:
                responsibility = responsibility[:idx].rstrip(",;:")
                break
        if len(words) > 14 and not responsibility.endswith("."):
            responsibility = responsibility.rstrip(".")
    elif best_score == 0:
        # No lexical overlap: do not pretend a random JD line is the link.
        # Prefer an operational ask when the story looks ops/reliability oriented.
        blob = " ".join(story.source_bullets).lower()
        operational = next(
            (
                resp
                for resp in responsibilities
                if re.search(
                    r"\b(troubleshoot|monitor|incident|operat|pipeline|deploy|alert)\b",
                    resp,
                    re.I,
                )
                and re.search(
                    r"\b(troubleshoot|monitor|incident|pipeline|deploy|alert\w*|"
                    r"health|fail\w*|kafka|ansible)\b",
                    blob,
                    re.I,
                )
            ),
            None,
        )
        if operational:
            words = operational.split()
            responsibility = " ".join(words[:14]).rstrip(",;:")
            connection = "weak"
        else:
            responsibility = "Deliver reliable software for the posted role"
            connection = "weak"
    else:
        # Fixture/short JDs may lack responsibility lines; stay role-based, not token-based.
        responsibility = "Deliver reliable software for the posted role"
        connection = "weak"
    story.jd_responsibility = responsibility
    story.connection = connection
    return story


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
        metric=_metric_for_story(bullets),
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
        metric=_metric_for_story(bullets),
    )


def _score(story: StoryEvidence) -> int:
    base = len(story.overlap_terms) * 10 + (5 if story.metric else 0)
    # Prefer verified employment over projects when overlap is otherwise similar.
    if story.kind == "experience":
        base += 4
    if story.metric:
        base += 2
    if story.connection == "strong":
        base += 6
    elif story.jd_responsibility:
        base += 1
    return base


def select_stories(
    document: ResumeDocument, keywords: list[str], description: str = ""
) -> tuple[StoryEvidence | None, StoryEvidence | None, list[str]]:
    responsibilities = _extract_jd_responsibilities(description)
    stories: list[StoryEvidence] = []
    for experience in document.experience:
        story = _from_experience(experience, keywords, description)
        if story:
            stories.append(story)
    for project in document.projects:
        project_story = _from_project(project, keywords, description)
        if project_story:
            stories.append(project_story)
    # Score after provisional responsibility attach.
    for story in stories:
        _attach_responsibility(story, responsibilities)
    stories.sort(key=_score, reverse=True)
    if not stories:
        return None, None, []
    primary = stories[0]
    # Prefer a second employment story when scores are close.
    secondary = None
    for story in stories[1:]:
        if story.label == primary.label:
            continue
        if story.kind == "experience" or primary.kind != "experience":
            secondary = story
            break
    if secondary is None:
        secondary = next(
            (story for story in stories[1:] if story.label != primary.label),
            None,
        )
    # Re-bind responsibilities so primary and secondary do not narrate the same ask.
    primary = _attach_responsibility(primary, responsibilities)
    if secondary is not None:
        remaining = [
            resp
            for resp in responsibilities
            if not primary.jd_responsibility
            or primary.jd_responsibility.rstrip("…")[:48].lower()
            not in resp.lower()
        ]
        secondary = _attach_responsibility(secondary, remaining or responsibilities)
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
