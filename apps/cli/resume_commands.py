from __future__ import annotations

from pathlib import Path

import typer
from sqlalchemy.orm import Session

from apps.resume_generation.paths import (
    GENERATED_ROOT,
    WORD_CONVERSION_ROOT,
    default_source_path,
    desktop_pdf_path,
)
from apps.resume_generation.services.audit_service import (
    build_audit_report,
    format_audit_report,
)
from apps.resume_generation.services.generation_service import (
    generate_resume_for_job,
)
from apps.resume_generation.services.pdf_render_service import detect_pdf_engine
from apps.resume_generation.services.resume_fact_service import scan_resume_corpus
from apps.resume_generation.services.semantic_provider import semantic_status
from packages.core.db import SessionLocal

resumes_app = typer.Typer(help="Resume corpus scan and tailored generation")


@resumes_app.command("scan")
def scan_cmd(
    path: str | None = typer.Option(
        None, "--path", help="Resume source folder (DOCX only)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    rebuild: bool = typer.Option(False, "--rebuild"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Discover and index local DOCX resumes into the fact inventory."""
    source = Path(path) if path else default_source_path()
    session: Session | None = None if dry_run else SessionLocal()
    try:
        summary = scan_resume_corpus(
            session, path=source, dry_run=dry_run, rebuild=rebuild
        )
        if session is not None and not dry_run:
            session.commit()
        typer.echo(f"Source: {source}")
        typer.echo(f"Discovered: {summary.discovered}")
        typer.echo(f"Parsed: {summary.parsed}")
        typer.echo(f"Skipped: {summary.skipped}")
        typer.echo(f"Changed: {summary.changed}")
        typer.echo(f"Unchanged: {summary.unchanged}")
        typer.echo(f"Duplicate facts: {summary.duplicate_facts}")
        typer.echo(f"Conflict warnings: {summary.conflict_warnings}")
        if summary.errors:
            typer.echo(f"Errors: {len(summary.errors)}")
            if verbose:
                for err in summary.errors:
                    typer.echo(f"  - {err}")
        if dry_run:
            typer.echo("(dry-run: database not modified)")
    finally:
        if session is not None:
            session.close()


@resumes_app.command("doctor")
def doctor_cmd() -> None:
    """Report resume toolchain readiness (no secrets)."""
    source = default_source_path()
    desktop = desktop_pdf_path()
    engine = detect_pdf_engine()
    sem = semantic_status()

    docx_ok = False
    try:
        import docx  # noqa: F401

        docx_ok = True
    except ImportError:
        docx_ok = False

    pypdf_ok = False
    try:
        import pypdf  # noqa: F401

        pypdf_ok = True
    except ImportError:
        pypdf_ok = False

    typer.echo("Resume doctor")
    typer.echo(f"  Source folder: {source} ({'ok' if source.exists() else 'MISSING'})")
    typer.echo(f"  python-docx: {'ok' if docx_ok else 'MISSING'}")
    typer.echo(f"  Microsoft Word: {'ok' if engine.word_available else 'not found'}")
    typer.echo(
        f"  LibreOffice: {'ok' if engine.libreoffice_available else 'not found'}"
    )
    typer.echo(f"  Selected PDF engine: {engine.selected or 'NONE'}")
    if not engine.selected:
        typer.echo(f"    → {engine.detail}")
    WORD_CONVERSION_ROOT.mkdir(parents=True, exist_ok=True)
    typer.echo(f"  Word conversion workspace: {WORD_CONVERSION_ROOT}")
    typer.echo(
        "  One-time macOS setup: run `resumes authorize-word` once and approve "
        "Files and Folders access for that workspace only (not Full Disk Access)."
    )
    typer.echo(f"  PDF validator (pypdf): {'ok' if pypdf_ok else 'MISSING'}")
    typer.echo(
        f"  Output dir: {GENERATED_ROOT} "
        f"({'writable' if GENERATED_ROOT.parent.exists() else 'parent missing'})"
    )
    GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
    typer.echo(f"  Desktop PDF path: {desktop}")
    typer.echo(
        f"  Desktop parent writable: {'ok' if desktop.parent.exists() else 'MISSING'}"
    )
    typer.echo(f"  Semantic provider: {sem['provider']}")
    typer.echo(f"  Semantic model: {sem['model']}")
    typer.echo(f"  Semantic API key: {sem['api_key']}")


@resumes_app.command("authorize-word")
def authorize_word_cmd() -> None:
    """One-time Word Files-and-Folders grant for the stable conversion workspace.

    Opens a probe DOCX with Word activated so macOS can show its access dialog.
    Approve access only to the printed workspace path. Do not enable Full Disk
    Access. After a successful grant, normal generation should stay unattended.
    """
    import subprocess
    from docx import Document

    WORD_CONVERSION_ROOT.mkdir(parents=True, exist_ok=True)
    probe_docx = WORD_CONVERSION_ROOT / "_authorize_probe.docx"
    probe_pdf = WORD_CONVERSION_ROOT / "_authorize_probe.pdf"
    Document().save(probe_docx)
    if probe_pdf.exists():
        probe_pdf.unlink()
    typer.echo(f"Word conversion workspace: {WORD_CONVERSION_ROOT.resolve()}")
    typer.echo(
        "If macOS asks, grant Microsoft Word access to THAT folder only, then "
        "this command should finish by writing a probe PDF."
    )
    script = f'''
set docxPOSIX to POSIX file "{probe_docx.resolve()}"
set pdfPOSIX to POSIX file "{probe_pdf.resolve()}"
tell application "Finder"
    activate
    open docxPOSIX
end tell
delay 3
tell application "Microsoft Word"
    if (count of documents) is 0 then
        error "Word still cannot open the workspace. Grant Files and Folders access to {WORD_CONVERSION_ROOT.resolve()} and retry."
    end if
    set theDoc to document 1
    save as theDoc file name pdfPOSIX file format format PDF
    try
        close theDoc saving no
    end try
end tell
'''
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    ok = probe_pdf.exists() and probe_pdf.stat().st_size > 0
    try:
        if probe_docx.exists():
            probe_docx.unlink()
    except OSError:
        pass
    if ok:
        from apps.resume_generation.services.pdf_render_service import (
            mark_word_workspace_authorized,
        )

        mark_word_workspace_authorized()
        typer.echo(f"Authorization OK — probe PDF written: {probe_pdf}")
        try:
            probe_pdf.unlink()
        except OSError:
            pass
        return
    detail = (result.stderr or result.stdout or "").strip()
    typer.echo(f"Authorization incomplete: {detail or 'no PDF produced'}")
    typer.echo(
        "Until the stable workspace is granted, generation falls back to an "
        "already-authorized prior job folder so conversions can still run."
    )
    raise typer.Exit(code=1)


@resumes_app.command("audit")
def audit_cmd(
    job_id: str = typer.Argument(..., help="Canonical job posting ID"),
    track: str | None = typer.Option(
        None, "--track", help="dev|cloud|ml|csharp|general"
    ),
    deterministic_only: bool = typer.Option(
        True, "--deterministic-only/--allow-semantic", help="Audit planning mode"
    ),
) -> None:
    """Inspect generation plan, eligibility, and provenance without writing files."""
    session: Session = SessionLocal()
    try:
        report = build_audit_report(
            session,
            job_id,
            deterministic_only=deterministic_only,
            track=track,
        )
        typer.echo(format_audit_report(report))
        if not report.permitted:
            raise typer.Exit(code=1)
    finally:
        session.close()


@resumes_app.command("generate")
def generate_cmd(
    job_id: str = typer.Argument(..., help="Canonical job posting ID"),
    track: str | None = typer.Option(
        None, "--track", help="dev|cloud|ml|csharp|general"
    ),
    deterministic_only: bool = typer.Option(False, "--deterministic-only"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    no_desktop_copy: bool = typer.Option(False, "--no-desktop-copy"),
) -> None:
    """Generate a tailored one-page ATS resume for a job."""
    session: Session = SessionLocal()
    try:
        result = generate_resume_for_job(
            session,
            job_id,
            track=track,
            deterministic_only=deterministic_only,
            dry_run=dry_run,
            copy_to_desktop=not no_desktop_copy,
        )
        session.commit()
        typer.echo(f"Status: {result.status}")
        if result.track:
            typer.echo(f"Track: {result.track}")
        if result.provider:
            typer.echo(f"Provider: {result.provider}")
        if result.docx_path:
            typer.echo(f"DOCX: {result.docx_path}")
        if result.pdf_path:
            typer.echo(f"PDF: {result.pdf_path}")
        if result.desktop_pdf_path:
            typer.echo(f"Desktop PDF: {result.desktop_pdf_path}")
        if result.source_files:
            typer.echo(f"Sources: {', '.join(result.source_files)}")
        if result.warnings:
            typer.echo("Warnings:")
            for w in result.warnings:
                typer.echo(f"  - {w}")
        if result.gaps:
            typer.echo("Gaps:")
            for g in result.gaps:
                typer.echo(f"  - {g}")
        if result.validation:
            typer.echo(f"Validation ok: {result.validation.ok}")
            for m in result.validation.messages:
                typer.echo(f"  - {m}")
        if result.error:
            typer.echo(f"Error: {result.error}")
            raise typer.Exit(code=1)
        if result.status in {"error", "validation_failed", "blocked"}:
            raise typer.Exit(code=1)
    finally:
        session.close()
