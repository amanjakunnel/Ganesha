from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from apps.resume_generation.services.docx_render_service import (
    extract_docx_text,
    render_docx,
)
from apps.resume_generation.services.pdf_render_service import convert_docx_to_pdf
from apps.resume_generation.services.resume_composer_service import ComposedResume
from pypdf import PdfWriter


def test_docx_generated_contains_sections(tmp_path: Path) -> None:
    composed = ComposedResume(
        name_line="Alex Michael Manjakunnel",
        contact_line="alex@example.com | linkedin.com/in/example",
        summary="Software engineer targeting Backend roles.",
        skills=["Python, Java"],
        experience=["Built REST APIs in Python."],
        projects=["TacoDB relational DBMS."],
        education=["B.S. Computer Science"],
        fact_ids=["a"],
    )
    path = render_docx(composed, tmp_path / "out.docx")
    assert path.exists()
    text = extract_docx_text(path)
    assert "Alex Michael Manjakunnel" in text
    assert "EXPERIENCE" in text.upper() or "Experience" in text
    assert "Python" in text


def test_pdf_conversion_mocked(tmp_path: Path) -> None:
    composed = ComposedResume(
        name_line="Alex Michael Manjakunnel",
        contact_line=None,
        summary=None,
        skills=["Python"],
        experience=["Did work."],
        projects=[],
        education=["B.S."],
        fact_ids=[],
    )
    docx = render_docx(composed, tmp_path / "x.docx")
    pdf_path = tmp_path / "x.pdf"

    def fake_convert(src: Path, dest: Path) -> Path:
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with dest.open("wb") as fh:
            writer.write(fh)
        return dest

    with patch(
        "apps.resume_generation.services.pdf_render_service.detect_pdf_engine"
    ) as det:
        from apps.resume_generation.services.pdf_render_service import PdfEngineStatus

        det.return_value = PdfEngineStatus(
            word_available=True,
            libreoffice_available=False,
            selected="microsoft_word",
            detail="mock",
        )
        with patch(
            "apps.resume_generation.services.pdf_render_service._convert_with_word",
            side_effect=lambda s, d: fake_convert(s, d),
        ):
            out = convert_docx_to_pdf(docx, pdf_path)
    assert out.exists()
    assert out.stat().st_size > 0
