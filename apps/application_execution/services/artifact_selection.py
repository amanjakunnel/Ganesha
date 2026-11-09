from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from apps.application_execution.schemas import ArtifactRef
from apps.cover_letter_generation.paths import (
    OUTPUT_BASENAME as COVER_BASENAME,
    resolve_cover_letters_dir,
)
from apps.dashboard.services.jobs_dashboard_service import job_url_for
from packages.core.domain.models import JobPosting, ResumeGeneration


def _desktop_basename(path: str | None) -> bool:
    if not path:
        return False
    p = Path(path)
    # Never treat a lone Desktop PDF as the job-specific artifact.
    return "Desktop" in p.parts and p.parent.name == "Desktop"


def select_resume_artifact(session: Session, job_id: str) -> ArtifactRef | None:
    """Pick the latest successful, active, job-specific resume generation."""
    rows = (
        session.query(ResumeGeneration)
        .filter(
            ResumeGeneration.job_posting_id == job_id,
            ResumeGeneration.status == "succeeded",
            ResumeGeneration.quality_status == "active",
        )
        .order_by(ResumeGeneration.created_at.desc())
        .all()
    )
    for row in rows:
        pdf = (row.pdf_path or "").strip() or None
        docx = (row.docx_path or "").strip() or None
        if not pdf and not docx:
            continue
        if pdf and _desktop_basename(pdf) and not docx:
            continue
        # Prefer internal stamp paths over Desktop copies.
        if pdf and _desktop_basename(pdf):
            pdf = None
        validation = row.validation_json or {}
        ok = bool(validation.get("ok", True))
        if pdf and not Path(pdf).is_file():
            continue
        if docx and not Path(docx).is_file():
            continue
        return ArtifactRef(
            kind="resume",
            generation_id=row.id,
            docx_path=docx,
            pdf_path=pdf,
            validation_ok=ok,
            reusable=True,
            note="Job-specific validated resume generation",
        )
    return None


def select_cover_letter_artifact(job_id: str) -> ArtifactRef | None:
    """Pick the latest job-specific cover-letter stamp with a PDF or DOCX."""
    root = resolve_cover_letters_dir() / job_id
    if not root.is_dir():
        return None
    stamps = sorted(
        [p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")],
        key=lambda p: p.name,
        reverse=True,
    )
    for stamp_dir in stamps:
        docx = stamp_dir / f"{COVER_BASENAME}.docx"
        pdf = stamp_dir / f"{COVER_BASENAME}.pdf"
        validation = stamp_dir / "cover_letter_validation.json"
        ok = True
        if validation.is_file():
            try:
                import json

                payload = json.loads(validation.read_text(encoding="utf-8"))
                ok = bool(payload.get("ok", True))
            except (OSError, ValueError):
                ok = False
        if not docx.is_file() and not pdf.is_file():
            continue
        if not ok:
            continue
        return ArtifactRef(
            kind="cover_letter",
            stamp=stamp_dir.name,
            docx_path=str(docx) if docx.is_file() else None,
            pdf_path=str(pdf) if pdf.is_file() else None,
            validation_ok=ok,
            reusable=True,
            note="Job-specific cover letter stamp",
        )
    return None


def application_destination(job: JobPosting) -> tuple[str | None, str | None]:
    """Return (application_url, posting_url)."""
    apply = (job.company_apply_url or "").strip() or None
    posting = (job.canonical_url or "").strip() or None
    destination = job_url_for(job)
    # Prefer explicit apply URL for the form destination.
    application_url = apply or destination
    return application_url, posting or destination
