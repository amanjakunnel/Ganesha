from __future__ import annotations

from pathlib import Path

from apps.cover_letter_generation.schemas import CoverLetterDocument
from apps.cover_letter_generation.services.cover_letter_docx_render_service import (
    render_cover_letter_docx,
)
from apps.resume_generation.services.ats_validation_service import extract_docx_text
from docx import Document


def test_docx_is_single_column_letter_without_tables(tmp_path: Path) -> None:
    letter = CoverLetterDocument(
        name_line="Alex Michael Manjakunnel",
        contact_line="State College, PA | alex@example.com",
        date_line="October 26, 2026",
        salutation="Dear Northwind Labs Hiring Team,",
        paragraphs=[
            "I am applying for the Software Engineer position at Northwind Labs.",
            "The strongest related example is verified platform work.",
            "I would welcome a conversation about that evidence.",
        ],
        company="Northwind Labs",
        role="Software Engineer",
        voice="technical_direct",
        fact_ids=["f1"],
        company_fact_ids=[],
    )
    path = render_cover_letter_docx(letter, tmp_path / "letter.docx")
    document = Document(str(path))
    assert document.tables == []
    assert list(document.inline_shapes) == []
    section = document.sections[0]
    assert section.page_width is not None and int(section.page_width) > 7_000_000
    text = extract_docx_text(path)
    assert "COVER LETTER" not in text.upper()
    assert "Dear Northwind Labs Hiring Team," in text
    assert "Sincerely," in text
    assert text.lower().count("alex michael manjakunnel") == 2
