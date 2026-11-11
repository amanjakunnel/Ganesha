from __future__ import annotations

import json
from pathlib import Path

import typer
from sqlalchemy.orm import Session

from apps.cover_letter_generation.paths import (
    desktop_pdf_path,
    resolve_cover_letters_dir,
)
from apps.cover_letter_generation.services.cover_letter_generation_service import (
    generate_cover_letter_for_job,
)
from apps.cover_letter_generation.services.cover_letter_plan_service import (
    build_cover_letter_plan,
)
from apps.cover_letter_generation.services.cover_letter_provenance_service import (
    plan_to_public_dict,
)
from apps.cover_letter_generation.services.cover_letter_validation_service import (
    validate_cover_artifacts,
)
from apps.cover_letter_generation.schemas import CoverLetterDocument, CoverLetterPlan
from apps.resume_generation.services.pdf_render_service import detect_pdf_engine
from apps.resume_generation.services.semantic_provider import semantic_status
from packages.core.db import SessionLocal
from packages.core.domain.models import JobPosting

cover_letters_app = typer.Typer(help="On-demand cover-letter generation")


def _print_plan(payload: dict[str, object]) -> None:
    typer.echo(f"Company: {payload.get('company')}")
    typer.echo(f"Role: {payload.get('role')}")
    typer.echo(f"Voice: {payload.get('voice')}")
    typer.echo(f"Resume track: {payload.get('resume_track')}")
    typer.echo(f"Used generated resume: {payload.get('used_resume_generation')}")
    primary = payload.get("primary") or {}
    secondary = payload.get("secondary") or {}
    if isinstance(primary, dict):
        typer.echo(f"Primary: {primary.get('label')}")
    if isinstance(secondary, dict):
        typer.echo(f"Secondary: {secondary.get('label')}")
    evidence = payload.get("company_evidence") or []
    typer.echo(f"Company evidence: {len(evidence) if isinstance(evidence, list) else 0}")
    warnings = payload.get("warnings") or []
    if isinstance(warnings, list):
        for warning in warnings:
            typer.echo(f"Warning: {warning}")
    if payload.get("block_reason"):
        typer.echo(f"Blocked: {payload.get('block_reason')}")


@cover_letters_app.command("doctor")
def doctor_cmd() -> None:
    """Report cover-letter toolchain readiness without secrets."""
    folder = resolve_cover_letters_dir()
    engine = detect_pdf_engine()
    sem = semantic_status()
    docx_ok = False
    try:
        import docx  # noqa: F401

        docx_ok = True
    except ImportError:
        docx_ok = False
    typer.echo(f"Cover letter folder: {folder}")
    typer.echo(f"Folder exists: {folder.is_dir()}")
    typer.echo(f"Desktop PDF: {desktop_pdf_path()}")
    typer.echo(f"python-docx: {'yes' if docx_ok else 'no'}")
    typer.echo(f"PDF engine: {engine.selected or 'none'} ({engine.detail})")
    typer.echo(
        f"Semantic provider: {sem['provider']} model={sem['model']} api_key={sem['api_key']}"
    )


@cover_letters_app.command("plan")
def plan_cmd(
    job_id: str,
    voice: str = typer.Option("technical_direct", "--voice"),
    skip_company_research: bool = typer.Option(False, "--skip-company-research"),
) -> None:
    """Show the evidence plan. Does not write a letter."""
    session: Session = SessionLocal()
    try:
        job = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
        if job is None:
            typer.echo("Job not found")
            raise typer.Exit(code=1)
        plan = build_cover_letter_plan(
            session,
            job,
            voice=voice,
            skip_company_research=skip_company_research,
        )
        _print_plan(plan_to_public_dict(plan))
        if plan.block_reason:
            raise typer.Exit(code=1)
    finally:
        session.close()


@cover_letters_app.command("generate")
def generate_cmd(
    job_id: str,
    voice: str = typer.Option("technical_direct", "--voice"),
    deterministic_only: bool = typer.Option(False, "--deterministic-only"),
    no_desktop_copy: bool = typer.Option(False, "--no-desktop-copy"),
    skip_company_research: bool = typer.Option(False, "--skip-company-research"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Generate a cover letter for one job."""
    session: Session = SessionLocal()
    try:
        result = generate_cover_letter_for_job(
            session,
            job_id,
            voice=voice,
            deterministic_only=deterministic_only,
            copy_to_desktop=not no_desktop_copy,
            skip_company_research=skip_company_research,
            dry_run=dry_run,
        )
        session.commit()
        typer.echo(f"Status: {result.status}")
        typer.echo(f"Voice: {result.voice}")
        typer.echo(f"Provider: {result.provider}")
        typer.echo(f"Evidence count: {result.evidence_count}")
        typer.echo(f"Company evidence used: {result.company_evidence_used}")
        if result.docx_path:
            typer.echo(f"DOCX: {result.docx_path}")
        if result.pdf_path:
            typer.echo(f"PDF: {result.pdf_path}")
        if result.desktop_pdf_path:
            typer.echo(f"Desktop PDF: {result.desktop_pdf_path}")
        for warning in result.warnings:
            typer.echo(f"Warning: {warning}")
        if result.validation:
            typer.echo(f"Validation ok: {result.validation.ok}")
            for message in result.validation.messages:
                typer.echo(f"  - {message}")
        if result.error:
            typer.echo(f"Error: {result.error}")
        if result.status != "succeeded" and result.status != "dry_run":
            raise typer.Exit(code=1)
    finally:
        session.close()


@cover_letters_app.command("validate")
def validate_cmd(artifact_path: str) -> None:
    """Re-check a generated cover-letter directory or PDF sibling pair."""
    path = Path(artifact_path)
    directory = path if path.is_dir() else path.parent
    docx = directory / "Alex_Michael_Manjakunnel_Cover.docx"
    pdf = directory / "Alex_Michael_Manjakunnel_Cover.pdf"
    meta_path = directory / "cover_letter_metadata.json"
    if not docx.exists():
        typer.echo("DOCX not found")
        raise typer.Exit(code=1)
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    plan = CoverLetterPlan(
        job_id=str(metadata.get("job_id") or ""),
        company=str(metadata.get("company") or ""),
        role=str(metadata.get("role") or ""),
        voice=str(metadata.get("voice") or "technical_direct"),
        role_family=str(metadata.get("role_family") or "general"),
        resume_track=metadata.get("resume_track") if isinstance(metadata.get("resume_track"), str) else None,
        used_resume_generation=bool(metadata.get("used_resume_generation")),
        contact_line=None,
        primary=None,
        secondary=None,
    )
    from apps.resume_generation.services.docx_render_service import extract_docx_text

    text = extract_docx_text(docx)
    paragraphs = [line.strip() for line in text.splitlines() if len(line.strip()) > 80]
    document = CoverLetterDocument(
        name_line="Alex Michael Manjakunnel",
        contact_line=None,
        date_line="",
        salutation="",
        paragraphs=paragraphs[:3],
        company=plan.company,
        role=plan.role,
        voice=plan.voice,
        fact_ids=[],
        company_fact_ids=[],
    )
    report = validate_cover_artifacts(
        docx_path=docx,
        pdf_path=pdf,
        document=document,
        plan=plan,
        allowed_numbers=set(),
        check_prose=False,
    )
    typer.echo(f"Validation ok: {report.ok}")
    for message in report.messages:
        typer.echo(f"  - {message}")
    if not report.ok:
        raise typer.Exit(code=1)
