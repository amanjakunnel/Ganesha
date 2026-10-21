from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PdfEngineStatus:
    word_available: bool
    libreoffice_available: bool
    selected: str | None
    detail: str


def _which(name: str) -> str | None:
    return shutil.which(name)


def detect_pdf_engine() -> PdfEngineStatus:
    word = False
    # macOS Word via osascript / Applications check
    word_app = Path("/Applications/Microsoft Word.app")
    if word_app.exists():
        word = True
    lo = _which("soffice") or _which("libreoffice")
    if word:
        return PdfEngineStatus(
            word_available=True,
            libreoffice_available=bool(lo),
            selected="microsoft_word",
            detail="Microsoft Word.app detected",
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


def _convert_with_word(docx_path: Path, pdf_path: Path) -> None:
    """Convert via Microsoft Word AppleScript on macOS."""
    docx_abs = str(docx_path.resolve())
    pdf_abs = str(pdf_path.resolve())
    if pdf_path.exists():
        pdf_path.unlink()
    # Prefer save-as PDF; treat PDF presence as success even if close is flaky.
    script = f'''
set docxPOSIX to POSIX file "{docx_abs}"
set pdfPOSIX to POSIX file "{pdf_abs}"
tell application "Microsoft Word"
    activate
    open docxPOSIX
    delay 2
    set theDoc to active document
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
    if pdf_path.exists() and pdf_path.stat().st_size > 0:
        return
    detail = (result.stderr or result.stdout or "").strip()
    raise RuntimeError(f"Word PDF conversion failed: {detail}")


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
    if status.selected == "microsoft_word":
        _convert_with_word(docx_path, pdf_path)
    else:
        lo = _which("soffice") or _which("libreoffice")
        assert lo is not None
        _convert_with_libreoffice(docx_path, pdf_path, lo)
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise RuntimeError("PDF conversion produced an empty or missing file")
    return pdf_path
