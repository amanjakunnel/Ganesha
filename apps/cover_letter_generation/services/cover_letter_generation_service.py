from __future__ import annotations

import shutil
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from apps.cover_letter_generation.paths import (
    OUTPUT_BASENAME,
    desktop_pdf_path,
    failed_root,
    resolve_cover_letters_dir,
)
from apps.cover_letter_generation.schemas import CoverGenerationResult, CoverValidation
from apps.cover_letter_generation.services.company_research_service import Transport
from apps.cover_letter_generation.services.cover_letter_composer_service import (
    allowed_numbers,
    compose_deterministic,
    prose_problems,
    style_warnings,
    try_model_prose,
)
from apps.cover_letter_generation.services.cover_letter_docx_render_service import (
    render_cover_letter_docx,
)
from apps.cover_letter_generation.services.cover_letter_pdf_render_service import (
    render_cover_letter_pdf,
)
from apps.cover_letter_generation.services.cover_letter_plan_service import (
    build_cover_letter_plan,
)
from apps.cover_letter_generation.services.cover_letter_provenance_service import (
    plan_to_public_dict,
    write_generation_metadata,
)
from apps.cover_letter_generation.services.cover_letter_validation_service import (
    validate_cover_artifacts,
)
from apps.resume_generation.services.ats_validation_service import atomic_copy_to_desktop
from packages.core.domain.models import JobPosting
from packages.core.settings import settings


def _evidence_count(plan_primary: object, plan_secondary: object) -> int:
    count = 0
    for story in (plan_primary, plan_secondary):
        if story is not None:
            count += 1
    return count


def generate_cover_letter_for_job(
    session: Session,
    job_id: str,
    *,
    voice: str | None = None,
    deterministic_only: bool = False,
    copy_to_desktop: bool = True,
    skip_company_research: bool = False,
    dry_run: bool = False,
    transport: Transport | None = None,
) -> CoverGenerationResult:
    job = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
    if job is None:
        return CoverGenerationResult(
            status="error",
            job_id=job_id,
            error="Job not found",
        )
    plan = build_cover_letter_plan(
        session,
        job,
        voice=voice,
        skip_company_research=skip_company_research,
        transport=transport,
    )
    public = plan_to_public_dict(plan)
    evidence_count = _evidence_count(plan.primary, plan.secondary)
    if plan.block_reason:
        return CoverGenerationResult(
            status="blocked",
            job_id=job_id,
            voice=plan.voice,
            evidence_count=evidence_count,
            warnings=plan.warnings,
            error=plan.block_reason,
            plan=public,
        )
    if dry_run:
        return CoverGenerationResult(
            status="dry_run",
            job_id=job_id,
            voice=plan.voice,
            evidence_count=evidence_count,
            company_evidence_used=bool(plan.company_evidence),
            warnings=plan.warnings,
            plan=public,
        )

    description = f"{job.title}\n{job.description_text or ''}"
    numbers = allowed_numbers(plan, description)
    provider = "deterministic"
    warnings = list(plan.warnings)
    modeled = try_model_prose(
        plan,
        job_description=description,
        deterministic_only=deterministic_only,
    )
    if modeled is not None:
        modeled.date_line = f"{datetime.now():%B} {datetime.now().day}, {datetime.now():%Y}"
        document = modeled
        provider = "anthropic"
    else:
        provider_name = (settings.semantic_fit_provider or "none").strip().lower()
        if (
            not deterministic_only
            and provider_name == "anthropic"
            and (settings.anthropic_api_key or "").strip()
        ):
            warnings.append(
                "Deterministic letter used. Model prose was unavailable or rejected."
            )
        document = compose_deterministic(plan)
    warnings.extend(style_warnings(document))
    problems = prose_problems(document, plan, allowed_numbers=numbers)
    if problems:
        return CoverGenerationResult(
            status="validation_failed",
            job_id=job_id,
            voice=plan.voice,
            evidence_count=evidence_count,
            company_evidence_used=bool(document.company_fact_ids),
            warnings=warnings,
            error="; ".join(problems),
            validation=CoverValidation(ok=False, messages=problems),
            plan=public,
            provider=provider,
        )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = resolve_cover_letters_dir() / job_id / stamp
    docx_path = out_dir / f"{OUTPUT_BASENAME}.docx"
    pdf_path = out_dir / f"{OUTPUT_BASENAME}.pdf"
    try:
        render_cover_letter_docx(document, docx_path)
        render_cover_letter_pdf(docx_path, pdf_path)
        validation = validate_cover_artifacts(
            docx_path=docx_path,
            pdf_path=pdf_path,
            document=document,
            plan=plan,
            allowed_numbers=numbers,
        )
    except Exception as exc:
        return CoverGenerationResult(
            status="error",
            job_id=job_id,
            voice=plan.voice,
            evidence_count=evidence_count,
            warnings=warnings,
            error=f"{exc.__class__.__name__}: {exc}",
            plan=public,
            provider=provider,
        )

    write_generation_metadata(out_dir, plan, validation, provider=provider)
    if not validation.ok:
        fail_dir = failed_root() / job_id / stamp
        fail_dir.mkdir(parents=True, exist_ok=True)
        for path in (docx_path, pdf_path):
            if path.exists():
                shutil.copy2(path, fail_dir / path.name)
        return CoverGenerationResult(
            status="validation_failed",
            job_id=job_id,
            voice=plan.voice,
            docx_path=str(docx_path),
            pdf_path=str(pdf_path) if pdf_path.exists() else None,
            evidence_count=evidence_count,
            company_evidence_used=bool(document.company_fact_ids),
            warnings=warnings,
            error="; ".join(validation.messages),
            validation=validation,
            plan=public,
            provider=provider,
        )

    desktop = None
    if copy_to_desktop:
        dest = atomic_copy_to_desktop(pdf_path, desktop_pdf_path())
        desktop = str(dest)
    return CoverGenerationResult(
        status="succeeded",
        job_id=job_id,
        voice=plan.voice,
        docx_path=str(docx_path),
        pdf_path=str(pdf_path),
        desktop_pdf_path=desktop,
        evidence_count=evidence_count,
        company_evidence_used=bool(document.company_fact_ids),
        warnings=warnings,
        validation=validation,
        plan=public,
        provider=provider,
    )
