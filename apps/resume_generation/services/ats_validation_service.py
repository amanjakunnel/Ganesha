from __future__ import annotations

import re
import shutil
from pathlib import Path

from pypdf import PdfReader

from apps.resume_generation.schemas import ValidationReport
from apps.resume_generation.services.docx_render_service import extract_docx_text

FORBIDDEN_SNIPPETS = (
    "prompt version",
    "semantic provider",
    "anthropic",
    "api_key",
    "work authorization",
    "cannot generate",
    "internal score",
    "source_document",
    "fact_id",
    "ganesha debug",
)

REQUIRED_MARKERS = ("experience", "education")


def pdf_page_count(path: Path) -> int:
    reader = PdfReader(str(path))
    return len(reader.pages)


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def is_valid_pdf(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 64:
        return False
    try:
        head = path.read_bytes()[:5]
        if head != b"%PDF-":
            return False
        PdfReader(str(path))
        return True
    except Exception:
        return False


def validate_artifacts(
    *,
    docx_path: Path,
    pdf_path: Path,
    expected_name: str = "Alex Michael Manjakunnel",
) -> ValidationReport:
    messages: list[str] = []
    docx_ok = False
    pdf_ok = False
    text_ok = False
    page_count: int | None = None

    try:
        docx_text = extract_docx_text(docx_path)
        docx_ok = bool(docx_text.strip())
        if not docx_ok:
            messages.append("DOCX text extraction is empty")
    except Exception as exc:
        messages.append(f"DOCX parse failed: {exc.__class__.__name__}")
        docx_text = ""

    if not pdf_path.exists():
        messages.append("PDF file missing")
    elif pdf_path.stat().st_size == 0:
        messages.append("PDF file is empty")
    elif not is_valid_pdf(pdf_path):
        messages.append("PDF is invalid or unreadable")
    else:
        try:
            page_count = pdf_page_count(pdf_path)
            if page_count != 1:
                messages.append(f"PDF has {page_count} pages; exactly 1 required")
            pdf_text = extract_pdf_text(pdf_path)
            if len(pdf_text.strip()) < 40:
                messages.append("PDF text extraction nearly empty")
            else:
                pdf_ok = page_count == 1
                combined = f"{docx_text}\n{pdf_text}".lower()
                if expected_name.lower() not in combined:
                    messages.append("Candidate name missing from extracted text")
                missing = [m for m in REQUIRED_MARKERS if m not in combined]
                if missing:
                    messages.append(
                        f"Missing sections in extracted text: {', '.join(missing)}"
                    )
                for snippet in FORBIDDEN_SNIPPETS:
                    if snippet in combined:
                        messages.append(f"Forbidden internal content leaked: {snippet}")
                # Decorative / odd bullets
                if re.search(r"[\u2600-\u27BF]", pdf_text):
                    messages.append("Unusual Unicode decorative characters detected")
                text_ok = (
                    expected_name.lower() in combined
                    and not missing
                    and not any(s in combined for s in FORBIDDEN_SNIPPETS)
                )
        except Exception as exc:
            messages.append(f"PDF validation failed: {exc.__class__.__name__}")

    ok = docx_ok and pdf_ok and text_ok and not messages
    if ok:
        messages.append("Validation passed: one-page ATS-readable PDF")
    return ValidationReport(
        ok=ok,
        page_count=page_count,
        docx_ok=docx_ok,
        pdf_ok=pdf_ok,
        text_ok=text_ok,
        messages=messages,
    )


def atomic_copy_to_desktop(src_pdf: Path, desktop_path: Path) -> Path:
    """Atomically replace Desktop PDF only after validating the source."""
    if not is_valid_pdf(src_pdf):
        raise RuntimeError("Refusing Desktop copy: source PDF invalid")
    if pdf_page_count(src_pdf) != 1:
        raise RuntimeError("Refusing Desktop copy: PDF is not exactly one page")
    if src_pdf.stat().st_size == 0:
        raise RuntimeError("Refusing Desktop copy: empty PDF")

    desktop_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = desktop_path.parent / f".{desktop_path.name}.tmp"
    try:
        shutil.copy2(src_pdf, tmp)
        if not is_valid_pdf(tmp) or tmp.stat().st_size == 0:
            raise RuntimeError("Temp Desktop PDF failed validation")
        if pdf_page_count(tmp) != 1:
            raise RuntimeError("Temp Desktop PDF is not one page")
        tmp.replace(desktop_path)
    finally:
        if tmp.exists() and tmp != desktop_path:
            try:
                tmp.unlink()
            except OSError:
                pass
    return desktop_path
