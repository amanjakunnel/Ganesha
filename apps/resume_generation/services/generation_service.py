from __future__ import annotations

import shutil
from datetime import UTC, datetime

from sqlalchemy.orm import Session, joinedload

from apps.resume_generation.paths import (
    FAILED_ROOT,
    GENERATED_ROOT,
    OUTPUT_BASENAME,
    TEMPLATE_VERSION,
    desktop_pdf_path,
)
from apps.resume_generation.schemas import GenerationResult
from apps.resume_generation.services.ats_validation_service import (
    atomic_copy_to_desktop,
    validate_artifacts,
)
from apps.resume_generation.services.docx_render_service import render_docx
from apps.resume_generation.services.job_requirement_service import (
    extract_job_requirements,
    job_description_hash,
)
from apps.resume_generation.services.pdf_render_service import convert_docx_to_pdf
from apps.resume_generation.services.provenance_service import build_audit_payload
from apps.resume_generation.services.resume_composer_service import (
    compose_resume,
    trim_for_page_budget,
)
from apps.resume_generation.services.resume_fact_service import usable_facts
from apps.resume_generation.services.resume_strategy_service import (
    build_resume_strategy,
)
from packages.core.domain.models import JobPosting, ResumeGeneration, make_uuid

INSUFFICIENT_MSG = (
    "Cannot generate a reliable tailored resume because this job does not "
    "contain enough job-description detail."
)


def preview_generation(
    session: Session,
    job_id: str,
    *,
    track: str | None = None,
    deterministic_only: bool = False,
) -> dict[str, object]:
    job = (
        session.query(JobPosting)
        .options(joinedload(JobPosting.company))
        .filter(JobPosting.id == job_id)
        .one_or_none()
    )
    if job is None:
        return {"error": "Job not found", "status": "error"}
    req = extract_job_requirements(job)
    strategy = build_resume_strategy(
        session,
        job,
        track_override=track,
        deterministic_only=deterministic_only,
    )
    facts = usable_facts(session)
    return {
        "status": "preview",
        "job_id": job.id,
        "title": job.title,
        "company": job.company.canonical_name if job.company else None,
        "track": strategy.track,
        "reason": strategy.reason,
        "evidence_count": len(facts),
        "provider": strategy.provider,
        "insufficient_description": req.insufficient_description,
        "warnings": strategy.warnings,
        "gaps": strategy.gaps,
        "source_files": strategy.source_files,
        "can_generate": bool(strategy.selected_fact_ids)
        and not (
            req.insufficient_description
            and len((job.description_text or "").strip()) < 40
        ),
        "block_reason": (
            INSUFFICIENT_MSG
            if req.insufficient_description
            and len((job.description_text or "").strip()) < 40
            else None
        ),
    }


def generate_resume_for_job(
    session: Session,
    job_id: str,
    *,
    track: str | None = None,
    deterministic_only: bool = False,
    dry_run: bool = False,
    copy_to_desktop: bool = True,
) -> GenerationResult:
    job = session.get(JobPosting, job_id)
    if job is None:
        return GenerationResult(status="error", error="Job not found")

    req = extract_job_requirements(job)
    if req.insufficient_description and len((job.description_text or "").strip()) < 40:
        return GenerationResult(status="error", error=INSUFFICIENT_MSG)

    strategy = build_resume_strategy(
        session,
        job,
        track_override=track,
        deterministic_only=deterministic_only,
    )
    if not strategy.selected_fact_ids:
        return GenerationResult(
            status="error",
            error="No verified resume facts available. Run: python -m apps.cli.main resumes scan",
            warnings=strategy.warnings,
            gaps=strategy.gaps,
            track=strategy.track,
            provider=strategy.provider,
        )

    if dry_run:
        return GenerationResult(
            status="dry_run",
            track=strategy.track,
            source_files=strategy.source_files,
            warnings=strategy.warnings,
            gaps=strategy.gaps,
            provider=strategy.provider,
        )

    composed = compose_resume(session, strategy, job_title=job.title)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = GENERATED_ROOT / job_id / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    docx_path = out_dir / f"{OUTPUT_BASENAME}.docx"
    pdf_path = out_dir / f"{OUTPUT_BASENAME}.pdf"

    gen = ResumeGeneration(
        id=make_uuid(),
        job_posting_id=job.id,
        status="rendering",
        selected_track=strategy.track,
        source_files_json=strategy.source_files,
        fact_ids_json=composed.fact_ids,
        job_description_hash=job_description_hash(job),
        template_version=TEMPLATE_VERSION,
        semantic_provider=strategy.provider,
        semantic_model=strategy.model,
        warnings_json=strategy.warnings,
        gaps_json=strategy.gaps,
    )
    session.add(gen)
    session.flush()

    try:
        warnings = list(strategy.warnings)
        validation = None
        for attempt in range(3):
            render_docx(composed, docx_path)
            convert_docx_to_pdf(docx_path, pdf_path)
            validation = validate_artifacts(docx_path=docx_path, pdf_path=pdf_path)
            if validation.ok:
                break
            if validation.page_count and validation.page_count > 1 and attempt < 2:
                composed = trim_for_page_budget(composed, pass_number=attempt + 1)
                warnings.extend(composed.omit_reasons[-3:])
                continue
            break

        assert validation is not None
        warnings.extend(composed.omit_reasons)
        deduped: list[str] = []
        seen_w: set[str] = set()
        for w in warnings:
            if w not in seen_w:
                seen_w.add(w)
                deduped.append(w)
        warnings = deduped
        gen.warnings_json = warnings

        gen.fact_ids_json = composed.fact_ids
        audit = build_audit_payload(
            strategy=strategy,
            composed=composed,
            validation=validation,
            template_version=TEMPLATE_VERSION,
            job_description_hash=job_description_hash(job),
        )
        gen.validation_json = audit["validation"]
        gen.docx_path = str(docx_path)
        gen.pdf_path = str(pdf_path)

        if not validation.ok:
            fail_dir = FAILED_ROOT / job_id / stamp
            fail_dir.mkdir(parents=True, exist_ok=True)
            for path in (docx_path, pdf_path):
                if path.exists():
                    shutil.copy2(path, fail_dir / path.name)
            gen.status = "validation_failed"
            gen.error_message = "; ".join(validation.messages)
            session.flush()
            return GenerationResult(
                status="validation_failed",
                generation_id=gen.id,
                track=strategy.track,
                docx_path=str(docx_path),
                pdf_path=str(pdf_path),
                source_files=strategy.source_files,
                warnings=warnings,
                gaps=strategy.gaps,
                validation=validation,
                error=gen.error_message,
                provider=strategy.provider,
            )

        desktop: str | None = None
        if copy_to_desktop:
            dest = desktop_pdf_path()
            atomic_copy_to_desktop(pdf_path, dest)
            desktop = str(dest)
            gen.desktop_pdf_path = desktop

        gen.status = "succeeded"
        session.flush()
        return GenerationResult(
            status="succeeded",
            generation_id=gen.id,
            track=strategy.track,
            docx_path=str(docx_path),
            pdf_path=str(pdf_path),
            desktop_pdf_path=desktop,
            source_files=strategy.source_files,
            warnings=warnings,
            gaps=strategy.gaps,
            validation=validation,
            provider=strategy.provider,
        )
    except Exception as exc:
        gen.status = "error"
        gen.error_message = f"{exc.__class__.__name__}: {exc}"
        session.flush()
        return GenerationResult(
            status="error",
            generation_id=gen.id,
            track=strategy.track,
            source_files=strategy.source_files,
            warnings=strategy.warnings,
            gaps=strategy.gaps,
            error=gen.error_message,
            provider=strategy.provider,
        )
