from __future__ import annotations

from pathlib import Path

from apps.resume_generation.services.pdf_render_service import convert_docx_to_pdf


def render_cover_letter_pdf(docx_path: Path, pdf_path: Path) -> Path:
    """Reuse the resume Word-then-LibreOffice conversion path."""
    return convert_docx_to_pdf(docx_path, pdf_path)
