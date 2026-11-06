from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from apps.resume_generation.paths import GENERATED_ROOT, OUTPUT_BASENAME, WORD_CONVERSION_ROOT

# Stable conversion workspace so macOS TCC grants for Word are not re-prompted
# for every new timestamped job folder. Grant Word access once via
# `resumes authorize-word`.
_WORD_LOCK_PATH = WORD_CONVERSION_ROOT / ".convert.lock"
_WORD_AUTH_MARKER = WORD_CONVERSION_ROOT / ".authorized"
_WORD_TIMEOUT_SECONDS = 90
_LOG = logging.getLogger(__name__)


@dataclass
class PdfEngineStatus:
    word_available: bool
    libreoffice_available: bool
    selected: str | None
    detail: str


@dataclass(frozen=True)
class WordWorkspaceChoice:
    path: Path
    mode: str  # permanent | fallback_stamp | permanent_unverified
    detail: str


def _which(name: str) -> str | None:
    return shutil.which(name)


def detect_pdf_engine() -> PdfEngineStatus:
    word = False
    word_app = Path("/Applications/Microsoft Word.app")
    if word_app.exists():
        word = True
    lo = _which("soffice") or _which("libreoffice")
    if word:
        choice = describe_word_workspace()
        return PdfEngineStatus(
            word_available=True,
            libreoffice_available=bool(lo),
            selected="microsoft_word",
            detail=(
                f"Microsoft Word.app detected; workspace mode={choice.mode} "
                f"path={choice.path} ({choice.detail})"
            ),
        )
    if lo:
        return PdfEngineStatus(
            word_available=False,
            libreoffice_available=True,
            selected="libreoffice",
            detail=f"LibreOffice at {lo}",
        )
    return PdfEngineStatus(
        word_available=False,
        libreoffice_available=False,
        selected=None,
        detail=(
            "No PDF converter found. Install Microsoft Word for Mac or LibreOffice "
            "(soffice) to enable DOCX→PDF conversion."
        ),
    )


def _escape_applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _recent_authorized_job_dir() -> Path | None:
    """Fallback staging: a prior stamp that already produced a PDF (TCC-granted).

    Never pick a brand-new stamp that only has a DOCX from the in-flight run —
    that folder has not been authorized yet and will re-prompt or hang.
    """
    candidates: list[Path] = []
    if not GENERATED_ROOT.exists():
        return None
    for job_dir in GENERATED_ROOT.iterdir():
        if not job_dir.is_dir() or job_dir.name.startswith("_"):
            continue
        for stamp in job_dir.iterdir():
            if not stamp.is_dir():
                continue
            pdf = stamp / f"{OUTPUT_BASENAME}.pdf"
            if pdf.exists() and pdf.stat().st_size > 0:
                candidates.append(stamp)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def permanent_workspace_authorized() -> bool:
    return _WORD_AUTH_MARKER.exists()


def describe_word_workspace(*, allow_fallback: bool | None = None) -> WordWorkspaceChoice:
    """Return the workspace that conversion will use, with an explicit mode label."""
    if allow_fallback is None:
        allow_fallback = os.environ.get("GANESHA_WORD_REQUIRE_PERMANENT", "").strip() not in {
            "1",
            "true",
            "yes",
        }
    WORD_CONVERSION_ROOT.mkdir(parents=True, exist_ok=True)
    if permanent_workspace_authorized():
        return WordWorkspaceChoice(
            path=WORD_CONVERSION_ROOT,
            mode="permanent",
            detail="authorized marker present; historical-stamp fallback disabled",
        )
    if not allow_fallback:
        return WordWorkspaceChoice(
            path=WORD_CONVERSION_ROOT,
            mode="permanent_unverified",
            detail=(
                "permanent workspace required but not authorized — run "
                "`resumes authorize-word`"
            ),
        )
    fallback = _recent_authorized_job_dir()
    if fallback is not None:
        return WordWorkspaceChoice(
            path=fallback,
            mode="fallback_stamp",
            detail=(
                "TRANSITIONAL: using historical stamp until "
                f"{WORD_CONVERSION_ROOT} is authorized via `resumes authorize-word`"
            ),
        )
    return WordWorkspaceChoice(
        path=WORD_CONVERSION_ROOT,
        mode="permanent_unverified",
        detail="no authorized stamp available; permanent workspace not yet granted",
    )


def resolve_word_workspace(*, allow_fallback: bool | None = None) -> Path:
    """Prefer the stable workspace once authorized; else a prior job stamp."""
    choice = describe_word_workspace(allow_fallback=allow_fallback)
    _LOG.info(
        "Word conversion workspace mode=%s path=%s (%s)",
        choice.mode,
        choice.path,
        choice.detail,
    )
    if choice.mode == "fallback_stamp":
        _LOG.warning("Word conversion using historical-stamp fallback: %s", choice.path)
    return choice.path


def mark_word_workspace_authorized() -> None:
    WORD_CONVERSION_ROOT.mkdir(parents=True, exist_ok=True)
    _WORD_AUTH_MARKER.write_text("ok\n", encoding="utf-8")


def _convert_with_word(docx_path: Path, pdf_path: Path) -> None:
    """Convert via Microsoft Word using Finder-open into a stable workspace.

    Word's direct AppleScript `open` often returns zero documents under TCC.
    Finder `open` plus Word `save as PDF` works once the folder is authorized.
    Final artifacts are copied to the job destination by Python afterward.
    """
    choice = describe_word_workspace()
    if choice.mode == "permanent_unverified" and os.environ.get(
        "GANESHA_WORD_REQUIRE_PERMANENT", ""
    ).strip() in {"1", "true", "yes"}:
        raise RuntimeError(
            "Permanent Word workspace required but not authorized. "
            f"Run: .venv/bin/python -m apps.cli.main resumes authorize-word "
            f"(workspace {WORD_CONVERSION_ROOT})"
        )
    workspace = resolve_word_workspace()
    workspace.mkdir(parents=True, exist_ok=True)
    work_id = uuid.uuid4().hex
    work_docx = workspace / f"_ganesha_convert_{work_id}.docx"
    work_pdf = workspace / f"_ganesha_convert_{work_id}.pdf"
    lock_path = _WORD_LOCK_PATH
    WORD_CONVERSION_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)

    if pdf_path.exists():
        pdf_path.unlink()

    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            shutil.copy2(docx_path, work_docx)
            docx_abs = _escape_applescript_string(str(work_docx.resolve()))
            pdf_abs = _escape_applescript_string(str(work_pdf.resolve()))
            # Finder open avoids Word's silent zero-document open under TCC.
            # Do not activate unless authorizing — activation can hang on dialogs.
            script = f'''
set docxPOSIX to POSIX file "{docx_abs}"
set pdfPOSIX to POSIX file "{pdf_abs}"
tell application "Finder"
    open docxPOSIX
end tell
delay 2.5
tell application "Microsoft Word"
    if (count of documents) is 0 then
        error "Word opened zero documents in {workspace}. Run: .venv/bin/python -m apps.cli.main resumes authorize-word"
    end if
    set theDoc to document 1
    save as theDoc file name pdfPOSIX file format format PDF
    try
        close theDoc saving no
    end try
end tell
'''
            try:
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    text=True,
                    timeout=_WORD_TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                _cleanup_work_files(work_docx, work_pdf)
                raise RuntimeError(
                    f"Word PDF conversion timed out after {_WORD_TIMEOUT_SECONDS}s "
                    f"staging in {workspace} (mode={choice.mode}). If a folder-access "
                    "dialog appeared, grant access once to that folder (or run "
                    f"`resumes authorize-word` for {WORD_CONVERSION_ROOT}), then retry. "
                    "Do not enable Full Disk Access."
                ) from exc

            if not (work_pdf.exists() and work_pdf.stat().st_size > 0):
                detail = (result.stderr or result.stdout or "").strip()
                _cleanup_work_files(work_docx, work_pdf)
                raise RuntimeError(
                    "Word PDF conversion failed "
                    f"(workspace {workspace}, mode={choice.mode}): "
                    f"{detail or 'empty PDF'}. "
                    f"One-time setup: `.venv/bin/python -m apps.cli.main resumes "
                    f"authorize-word` for {WORD_CONVERSION_ROOT}."
                )

            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(work_pdf, pdf_path)
            if workspace.resolve() == WORD_CONVERSION_ROOT.resolve():
                mark_word_workspace_authorized()
        finally:
            _cleanup_work_files(work_docx, work_pdf)
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    if not (pdf_path.exists() and pdf_path.stat().st_size > 0):
        raise RuntimeError("Word PDF conversion produced an empty destination file")


def _cleanup_work_files(*paths: Path) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def _convert_with_libreoffice(docx_path: Path, pdf_path: Path, lo_bin: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        result = subprocess.run(
            [
                lo_bin,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(docx_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"LibreOffice conversion failed: {(result.stderr or result.stdout or '').strip()}"
            )
        produced = out_dir / f"{docx_path.stem}.pdf"
        if not produced.exists():
            raise RuntimeError("LibreOffice did not produce a PDF")
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, pdf_path)


def convert_docx_to_pdf(docx_path: Path, pdf_path: Path) -> Path:
    status = detect_pdf_engine()
    if not status.selected:
        raise RuntimeError(status.detail)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        pdf_path.unlink()
    workspace_choice = (
        describe_word_workspace() if status.selected == "microsoft_word" else None
    )
    if status.selected == "microsoft_word":
        _convert_with_word(docx_path, pdf_path)
        # Re-read after conversion so a newly written .authorized marker is visible.
        workspace_choice = describe_word_workspace()
    else:
        lo = _which("soffice") or _which("libreoffice")
        assert lo is not None
        _convert_with_libreoffice(docx_path, pdf_path, lo)
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise RuntimeError("PDF conversion produced an empty or missing file")
    # Per-run diagnostics: never silently depend on fallback without a record.
    diagnostics = {
        "engine": status.selected,
        "engine_detail": status.detail,
        "word_workspace_mode": workspace_choice.mode if workspace_choice else None,
        "word_workspace_path": str(workspace_choice.path) if workspace_choice else None,
        "word_workspace_detail": workspace_choice.detail if workspace_choice else None,
        "fallback_used": bool(
            workspace_choice and workspace_choice.mode == "fallback_stamp"
        ),
        "permanent_workspace": str(WORD_CONVERSION_ROOT),
    }
    (pdf_path.parent / "word_conversion_diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2) + "\n",
        encoding="utf-8",
    )
    return pdf_path
