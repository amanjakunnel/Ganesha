from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from apps.cover_letter_generation.paths import CANDIDATE_NAME
from apps.cover_letter_generation.schemas import (
    CoverLetterDocument,
    CoverLetterPlan,
    CoverValidation,
)
from apps.cover_letter_generation.services.cover_letter_composer_service import (
    prose_problems,
    word_count,
)
from apps.resume_generation.services.ats_validation_service import (
    is_valid_pdf,
    pdf_page_count,
)
from apps.resume_generation.services.docx_render_service import extract_docx_text

_FORBIDDEN_OUTPUT = (
    "work authorization",
    "f-1",
    "stem opt",
    "sponsorship",
    "citizenship",
    "visa",
    "referral",
    "anthropic",
    "prompt version",
    "ganesha",
    "/users/",
    "http://",
    "https://",
    "www.",
)


def _letter_page_size_ok(pdf_path: Path) -> bool:
    reader = PdfReader(str(pdf_path))
    if not reader.pages:
        return False
    box = reader.pages[0].mediabox
    width = float(box.width)
    height = float(box.height)
    return abs(width - 612) <= 12 and abs(height - 792) <= 12


def validate_letter_content(
    document: CoverLetterDocument,
    plan: CoverLetterPlan,
    *,
    allowed_numbers: set[str],
) -> list[str]:
    return prose_problems(document, plan, allowed_numbers=allowed_numbers)


def validate_cover_artifacts(
    *,
    docx_path: Path,
    pdf_path: Path,
    document: CoverLetterDocument,
    plan: CoverLetterPlan,
    allowed_numbers: set[str],
    check_prose: bool = True,
) -> CoverValidation:
    messages: list[str] = []
    if check_prose:
        messages.extend(
            validate_letter_content(
                document, plan, allowed_numbers=allowed_numbers
            )
        )
    if not docx_path.exists():
        messages.append("DOCX is missing")
        return CoverValidation(ok=False, messages=messages, word_count=0)
    try:
        docx_text = extract_docx_text(docx_path)
    except Exception as exc:
        messages.append(f"DOCX parse failed: {exc.__class__.__name__}")
        docx_text = ""
    parsed = Document(str(docx_path))
    if parsed.tables:
        messages.append("DOCX contains a table")
    if parsed.inline_shapes:
        messages.append("DOCX contains an image")
    for section in parsed.sections:
        footer = " ".join(p.text for p in section.footer.paragraphs).strip()
        if footer:
            messages.append("DOCX contains a footer")
        width = section.page_width
        height = section.page_height
        if width is None or height is None:
            messages.append("DOCX page size is missing")
        elif abs(int(width) - 7772400) > 200000 or abs(int(height) - 10058400) > 200000:
            messages.append("DOCX page size is not U.S. Letter")

    lower = docx_text.lower()
    body_lower = "\n".join(document.paragraphs).lower()
    if docx_text.upper().count(CANDIDATE_NAME.upper()) < 2:
        messages.append("Expected name in the header and signature")
    if lower.count("dear ") != 1:
        messages.append("Expected exactly one salutation")
    if lower.count("sincerely") != 1:
        messages.append("Expected exactly one closing")
    if document.date_line.lower() not in lower:
        messages.append("Date line missing")
    for snippet in _FORBIDDEN_OUTPUT:
        if snippet in body_lower:
            messages.append(f"Forbidden content in letter: {snippet}")
    if re.search(r"\bopt\b", body_lower):
        messages.append("Forbidden content in letter: opt")
    paragraphs = [part.strip() for part in docx_text.split("\n") if part.strip()]
    body = [line for line in paragraphs if len(line) > 80]
    if len(body) != len(set(body)):
        messages.append("Duplicate paragraphs")

    page_count = None
    if not pdf_path.exists() or pdf_path.stat().st_size == 0 or not is_valid_pdf(pdf_path):
        messages.append("PDF is missing or invalid")
    else:
        page_count = pdf_page_count(pdf_path)
        if page_count != 1:
            messages.append(f"PDF has {page_count} pages; exactly 1 required")
        if not _letter_page_size_ok(pdf_path):
            messages.append("PDF page size is not U.S. Letter")
        pdf_text = "\n".join(
            page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages
        )
        if len(pdf_text.strip()) < 40:
            messages.append("PDF text extraction is empty")
        pdf_lower = pdf_text.lower()
        if document.contact_line:
            pdf_lower = pdf_lower.replace(document.contact_line.lower(), " ")
        for snippet in _FORBIDDEN_OUTPUT:
            if snippet in pdf_lower:
                messages.append(f"Forbidden content in PDF: {snippet}")

    count = word_count(" ".join(document.paragraphs))
    ok = not messages
    if ok:
        messages.append("Validation passed: one-page cover letter")
    return CoverValidation(ok=ok, messages=messages, page_count=page_count, word_count=count)
