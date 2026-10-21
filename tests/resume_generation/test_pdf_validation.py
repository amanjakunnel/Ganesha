from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from apps.resume_generation.schemas import (
    BulletRef,
    EducationEntry,
    ExperienceEntry,
    ResumeDocument,
)
from apps.resume_generation.services.docx_render_service import (
    extract_docx_text,
    render_docx,
)
from apps.resume_generation.services.pdf_render_service import convert_docx_to_pdf
from pypdf import PdfWriter


def _doc() -> ResumeDocument:
    return ResumeDocument(
        name_line="Alex Michael Manjakunnel",
        contact_line="alex@example.com | linkedin.com/in/example",
        summary=None,
        skill_lines=["Languages: Python, Java"],
        experience=[
            ExperienceEntry(
                company="Acme",
                title="Engineer",
                dates="2022 – Present",
                bullets=[
                    BulletRef(
                        text="Built REST APIs in Python.",
                        fact_id="a",
                        normalized="built rest apis in python.",
                    )
                ],
                parent_key="acme",
            )
        ],
        projects=[],
        education=[
            EducationEntry(
                institution="Example University",
                degree="B.S. Computer Science",
                parent_key="ex",
            )
        ],
    )


def test_docx_generated_contains_sections(tmp_path: Path) -> None:
    path = render_docx(_doc(), tmp_path / "out.docx")
    assert path.exists()
    text = extract_docx_text(path)
    assert "Alex Michael Manjakunnel" in text
    assert "EXPERIENCE" in text.upper()
    assert "Acme" in text
    assert "Python" in text


def test_pdf_conversion_mocked(tmp_path: Path) -> None:
    docx = render_docx(_doc(), tmp_path / "x.docx")
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
