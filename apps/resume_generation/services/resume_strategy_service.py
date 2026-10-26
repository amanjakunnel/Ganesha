from __future__ import annotations

from sqlalchemy.orm import Session

from apps.resume_generation.schemas import ResumeStrategy
from apps.resume_generation.services.job_requirement_service import (
    extract_job_requirements,
)
from apps.resume_generation.services.semantic_provider import get_semantic_provider
from apps.resume_generation.services.resume_fact_service import gpa_conflict_warning
from apps.resume_generation.services.structured_document_service import (
    build_resume_document,
    validate_document_structure,
)
from packages.core.domain.models import JobPosting, ResumeSourceDocument
from packages.core.settings import settings

TRACK_PRIORITY = ("dev", "cloud", "ml", "csharp", "general")


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
    if track not in (*TRACK_PRIORITY, "csharp"):
        track = "general"

    baseline = (
        session.query(ResumeSourceDocument)
        .filter(ResumeSourceDocument.track == track)
        .first()
    )
    if baseline is None and track != "general":
        baseline = (
            session.query(ResumeSourceDocument)
            .filter(ResumeSourceDocument.track == "general")
            .first()
        )
        if baseline:
            warnings.append(
                f"No '{track}' baseline DOCX indexed; falling back to general structure."
            )
            track = "general"

    document = build_resume_document(session, track=track, keywords=req.keywords)
    gpa_warn = gpa_conflict_warning(session)
    if gpa_warn:
        warnings.append(gpa_warn)
    structure_errors = validate_document_structure(document)
    if structure_errors:
        return ResumeStrategy(
            track=track,
            reason="Structured plan failed validation gates.",
            selected_fact_ids=document.fact_ids,
            source_files=document.source_files,
            outline={},
            gaps=gaps,
            warnings=warnings + structure_errors,
            provider=provider_name,
            document=document,
            block_reason=structure_errors[0],
        )

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
                        {"id": fid, "text": fid, "section": "selected"}
                        for fid in document.fact_ids[:25]
                    ],
                )
                if enriched:
                    provider_name = enriched.get("provider", provider.name)
                    model_name = enriched.get("model")
                    for g in enriched.get("gaps") or []:
                        if isinstance(g, str) and g not in gaps:
                            gaps.append(g)
                    warnings.append(
                        "Semantic provider assisted planning (claims unchanged)."
                    )
            except Exception as exc:
                warnings.append(
                    f"Semantic provider unavailable ({exc.__class__.__name__}); "
                    "used deterministic strategy."
                )
        else:
            warnings.append(
                "Semantic provider not configured; used deterministic strategy."
            )

    # Gaps for important missing keywords
    blob = " ".join(document.skill_lines).lower()
    blob += " " + " ".join(
        b.text.lower() for e in document.experience for b in e.bullets
    )
    for kw in (
        "kubernetes",
        "aws",
        "azure",
        "gcp",
        "pytorch",
        "terraform",
        "docker",
        "python",
    ):
        if kw in req.keywords and kw not in blob:
            gaps.append(f"No verified evidence for keyword: {kw}")

    reason = (
        f"Baseline track '{track}' with {len(document.experience)} experience entries, "
        f"{len(document.projects)} projects, {len(document.education)} education entries, "
        f"and {len(document.skill_lines)} skill categories."
    )
    outline = {
        "skills": document.skill_lines,
        "experience": [e.parent_key for e in document.experience],
        "projects": [p.parent_key for p in document.projects],
        "education": [e.parent_key for e in document.education],
    }
    return ResumeStrategy(
        track=track,
        reason=reason,
        selected_fact_ids=document.fact_ids,
        source_files=document.source_files,
        outline=outline,
        gaps=gaps[:12],
        warnings=warnings,
        provider=provider_name,
        model=model_name,
        document=document,
    )
