from __future__ import annotations

from sqlalchemy.orm import Session

from apps.resume_generation.schemas import ResumeStrategy
from apps.resume_generation.services.job_requirement_service import (
    extract_job_requirements,
)
from apps.resume_generation.services.resume_fact_service import usable_facts
from apps.resume_generation.services.semantic_provider import (
    get_semantic_provider,
)
from packages.core.domain.models import JobPosting, ResumeFact, ResumeSourceDocument
from packages.core.settings import settings

TRACK_PRIORITY = ("dev", "cloud", "ml", "csharp", "general")


def _score_fact(fact: ResumeFact, keywords: list[str]) -> int:
    text = fact.normalized_text
    score = 0
    for kw in keywords:
        if kw in text:
            score += 2
    for skill in fact.skills_json or []:
        if skill.lower() in keywords:
            score += 3
    if fact.verification_state == "corroborated":
        score += 1
    if fact.section in {"experience", "projects", "skills"}:
        score += 1
    return score


def _contact_and_education(facts: list[ResumeFact]) -> dict[str, list[ResumeFact]]:
    buckets: dict[str, list[ResumeFact]] = {
        "header": [],
        "education": [],
        "skills": [],
        "experience": [],
        "projects": [],
    }
    for fact in facts:
        if fact.section == "other" and len(buckets["header"]) < 3:
            buckets["header"].append(fact)
        elif fact.section in buckets:
            buckets[fact.section].append(fact)
    return buckets


def build_resume_strategy(
    session: Session,
    job: JobPosting,
    *,
    track_override: str | None = None,
    deterministic_only: bool = False,
) -> ResumeStrategy:
    req = extract_job_requirements(job)
    warnings: list[str] = []
    gaps: list[str] = []
    provider_name = "deterministic"
    model_name: str | None = None

    if req.insufficient_description:
        warnings.append(
            "Job description is thin; tailoring will use title/company signals only."
        )

    track = track_override or req.role_family
    if track not in TRACK_PRIORITY and track != "csharp":
        track = "general"

    facts = usable_facts(session)
    if not facts:
        return ResumeStrategy(
            track=track,
            reason="No scanned resume facts available. Run resumes scan first.",
            selected_fact_ids=[],
            source_files=[],
            outline={},
            gaps=["No resume corpus facts loaded"],
            warnings=warnings + ["Cannot generate without scanned DOCX evidence."],
            provider=provider_name,
        )

    # Prefer baseline track documents, then corroborated others (never company-specific alone)
    docs = {d.id: d for d in session.query(ResumeSourceDocument).all()}
    baseline_ids = {
        d.id
        for d in docs.values()
        if d.track == track and d.track != "company_specific_reference"
    }
    scored: list[tuple[int, ResumeFact]] = []
    for fact in facts:
        doc = docs.get(fact.source_document_id)
        if doc and doc.track == "company_specific_reference":
            # Supporting evidence only when corroborated / overlapping with baseline skills
            if fact.verification_state != "corroborated":
                continue
        score = _score_fact(fact, req.keywords)
        if baseline_ids and fact.source_document_id in baseline_ids:
            score += 2
        if (
            score > 0
            or fact.section in {"education", "skills"}
            or (fact.section == "other" and len(fact.source_text) < 80)
        ):
            scored.append((score, fact))
    scored.sort(key=lambda x: (-x[0], x[1].section, x[1].source_text))

    selected: list[ResumeFact] = []
    seen_norm: set[str] = set()
    for score, fact in scored:
        if fact.normalized_text in seen_norm:
            continue
        seen_norm.add(fact.normalized_text)
        selected.append(fact)
        if len(selected) >= 40:
            break

    # Ensure education/skills/header representation
    buckets = _contact_and_education(facts)
    for key in ("header", "education", "skills"):
        for fact in buckets[key][:4]:
            if fact.normalized_text not in seen_norm and fact.usable:
                selected.append(fact)
                seen_norm.add(fact.normalized_text)

    # Gaps: required keywords with no evidence
    present = " ".join(f.normalized_text for f in selected)
    for kw in req.keywords[:20]:
        if kw not in present and kw in {
            "kubernetes",
            "aws",
            "azure",
            "gcp",
            "pytorch",
            "tensorflow",
            "terraform",
            "java",
            "python",
            "docker",
        }:
            gaps.append(f"No verified evidence for keyword: {kw}")

    if not deterministic_only and settings.semantic_fit_provider.lower() != "none":
        provider = get_semantic_provider()
        if provider.available():
            try:
                enriched = provider.enrich_strategy(
                    job_title=job.title,
                    job_description=job.description_text or "",
                    track=track,
                    keywords=req.keywords,
                    fact_summaries=[
                        {"id": f.id, "text": f.source_text[:240], "section": f.section}
                        for f in selected[:25]
                    ],
                )
                if enriched:
                    provider_name = enriched.get("provider", provider.name)
                    model_name = enriched.get("model")
                    # Only accept reordering / gap notes; never new claim text
                    gap_extra = enriched.get("gaps") or []
                    for g in gap_extra:
                        if isinstance(g, str) and g not in gaps:
                            gaps.append(g)
                    warnings.append(
                        "Semantic provider assisted planning (claims unchanged)."
                    )
            except Exception as exc:
                warnings.append(
                    f"Semantic provider unavailable ({exc.__class__.__name__}); used deterministic strategy."
                )
        else:
            warnings.append(
                "Semantic provider not configured; used deterministic strategy."
            )

    source_files = sorted(
        {
            docs[f.source_document_id].filename
            for f in selected
            if f.source_document_id in docs
        }
    )
    reason = (
        f"Selected baseline track '{track}' from job-title/description signals "
        f"({req.role_family}); used {len(selected)} verified facts from {len(source_files)} DOCX sources."
    )
    outline = {
        "header": [f.id for f in selected if f.section == "other"][:3],
        "skills": [f.id for f in selected if f.section == "skills"][:6],
        "experience": [f.id for f in selected if f.section == "experience"][:12],
        "projects": [f.id for f in selected if f.section == "projects"][:6],
        "education": [f.id for f in selected if f.section == "education"][:8],
    }
    return ResumeStrategy(
        track=track,
        reason=reason,
        selected_fact_ids=[f.id for f in selected],
        source_files=source_files,
        outline=outline,
        gaps=gaps[:12],
        warnings=warnings,
        provider=provider_name,
        model=model_name,
    )
