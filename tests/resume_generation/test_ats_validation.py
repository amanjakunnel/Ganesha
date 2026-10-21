from __future__ import annotations

from pathlib import Path

from apps.resume_generation.schemas import (
    BulletRef,
    EducationEntry,
    ExperienceEntry,
    ResumeDocument,
)
from apps.resume_generation.services.ats_validation_service import (
    atomic_copy_to_desktop,
    is_valid_pdf,
    pdf_page_count,
    validate_artifacts,
)
from apps.resume_generation.services.docx_render_service import render_docx
from pypdf import PdfWriter


def _doc() -> ResumeDocument:
    return ResumeDocument(
        name_line="Alex Michael Manjakunnel",
        contact_line="alex@example.com",
        summary=None,
        skill_lines=["Languages: Python, Java"],
        experience=[
            ExperienceEntry(
                company="Acme Corp",
                title="Software Engineer",
                dates="Jan 2022 – Present",
                bullets=[
                    BulletRef(
                        text="Built REST APIs in Python.",
                        fact_id="1",
                        normalized="built rest apis in python.",
                    )
                ],
                parent_key="acme",
            )
        ],
        projects=[],
        education=[
            EducationEntry(
                institution="PES University",
                degree="Bachelor of Technology",
                major="Computer Science and Engineering",
                graduation_date="Jun 2023",
                parent_key="pes",
            )
        ],
    )


def _minimal_pdf(path: Path, pages: int = 1) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        writer.write(fh)
    return path


def test_validate_rejects_two_page_pdf(tmp_path: Path) -> None:
    docx = render_docx(_doc(), tmp_path / "r.docx")
    pdf = _minimal_pdf(tmp_path / "r.pdf", pages=2)
    report = validate_artifacts(docx_path=docx, pdf_path=pdf, document=_doc())
    assert report.ok is False
    assert report.page_count == 2


def test_invalid_pdf_not_copied_to_desktop(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    desktop = tmp_path / "Desktop" / "Alex_Michael_Manjakunnel_Resume.pdf"
    desktop.parent.mkdir(parents=True)
    try:
        atomic_copy_to_desktop(bad, desktop)
        assert False, "expected failure"
    except RuntimeError:
        assert not desktop.exists()


def test_one_page_valid_pdf_helpers(tmp_path: Path) -> None:
    pdf = _minimal_pdf(tmp_path / "one.pdf", pages=1)
    assert is_valid_pdf(pdf)
    assert pdf_page_count(pdf) == 1


def test_structural_docx_has_single_skills_heading(tmp_path: Path) -> None:
    path = render_docx(_doc(), tmp_path / "ok.docx")
    from apps.resume_generation.services.docx_render_service import extract_docx_text

    text = extract_docx_text(path)
    assert text.upper().count("SKILLS") == 1
    assert "Acme Corp" in text
    assert "PES University" in text
