from __future__ import annotations

from pathlib import Path

from apps.resume_generation.services.ats_validation_service import (
    atomic_copy_to_desktop,
    is_valid_pdf,
    pdf_page_count,
    validate_artifacts,
)
from apps.resume_generation.services.docx_render_service import render_docx
from apps.resume_generation.services.resume_composer_service import ComposedResume
from pypdf import PdfWriter


def _minimal_pdf(path: Path, pages: int = 1) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        writer.write(fh)
    return path


def test_validate_rejects_two_page_pdf(tmp_path: Path) -> None:
    composed = ComposedResume(
        name_line="Alex Michael Manjakunnel",
        contact_line="alex@example.com",
        summary=None,
        skills=["Python"],
        experience=["Built APIs at Acme."],
        projects=[],
        education=["B.S. CS"],
        fact_ids=["1"],
    )
    docx = render_docx(composed, tmp_path / "r.docx")
    pdf = _minimal_pdf(tmp_path / "r.pdf", pages=2)
    report = validate_artifacts(docx_path=docx, pdf_path=pdf)
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
