from __future__ import annotations

from pathlib import Path

from apps.cover_letter_generation.schemas import (
    CoverLetterDocument,
    CoverLetterPlan,
    StoryEvidence,
)
from apps.cover_letter_generation.services.cover_letter_docx_render_service import (
    render_cover_letter_docx,
)
from apps.cover_letter_generation.services.cover_letter_generation_service import (
    generate_cover_letter_for_job,
)
from apps.cover_letter_generation.services.cover_letter_validation_service import (
    validate_cover_artifacts,
)
from apps.resume_generation.services.resume_fact_service import scan_resume_corpus
from tests.cover_letter_generation.conftest import add_job, blank_pdf
from tests.resume_generation.conftest import build_standard_corpus


def _document() -> tuple[CoverLetterDocument, CoverLetterPlan]:
    story = StoryEvidence(
        kind="experience",
        label="Software Engineer at Acme Corp",
        employer="Acme Corp",
        title="Software Engineer",
        fact_ids=["fact-1"],
        source_bullets=["Built REST APIs in Python serving internal tooling."],
        overlap_terms=["python"],
    )
    paragraph = (
        "I am applying for the Software Engineer position at Northwind Labs. "
        "The technical requirement I am responding to is python. "
        "The description's emphasis on python matches work I have already done, "
        "which is the reason this role is a concrete fit. This letter uses the "
        "Software Engineer description and verified experience only. "
    )
    body = paragraph + (
        "The strongest related example is my work as Software Engineer at Acme Corp. "
        "The verified work involved python. That is relevant to the Software Engineer "
        "role at Northwind Labs because the posting asks for those capabilities, "
        "and I am not treating other tools in the description as experience I already have. "
        "A complementary example is the TacoDB project. The verified work involved sql. "
        "Together, those two examples are the scope of what I would bring to Northwind Labs. "
        "I would use that python experience to contribute quickly to the Software Engineer "
        "work at Northwind Labs, and only where the posting and the verified role use the "
        "same terms. The letter stays with those examples so the claim is limited to "
        "verified work at Acme Corp. I would welcome a short conversation about how that "
        "evidence applies."
    )
    document = CoverLetterDocument(
        name_line="Alex Michael Manjakunnel",
        contact_line="alex@example.com",
        date_line="October 26, 2026",
        salutation="Dear Northwind Labs Hiring Team,",
        paragraphs=[body, body, body],
        company="Northwind Labs",
        role="Software Engineer",
        voice="technical_direct",
        fact_ids=["fact-1"],
        company_fact_ids=[],
    )
    plan = CoverLetterPlan(
        job_id="job",
        company="Northwind Labs",
        role="Software Engineer",
        voice="technical_direct",
        role_family="dev",
        resume_track="dev",
        used_resume_generation=False,
        contact_line=document.contact_line,
        primary=story,
        secondary=None,
    )
    return document, plan


def test_two_page_pdf_fails_validation(tmp_path: Path) -> None:
    document, plan = _document()
    docx = render_cover_letter_docx(document, tmp_path / "letter.docx")
    pdf = blank_pdf(tmp_path / "letter.pdf", pages=2)
    report = validate_cover_artifacts(
        docx_path=docx,
        pdf_path=pdf,
        document=document,
        plan=plan,
        allowed_numbers=set(),
        check_prose=False,
    )
    assert report.ok is False
    assert any("pages" in message for message in report.messages)


def test_invalid_pdf_is_not_copied_to_desktop(session, tmp_path: Path, monkeypatch) -> None:
    root = build_standard_corpus(tmp_path / "resumes")
    scan_resume_corpus(session, path=root, rebuild=True)
    session.commit()
    job = add_job(session, company_name="DesktopCo")
    desktop = tmp_path / "Desktop" / "Alex_Michael_Manjakunnel_Cover.pdf"

    def fake_pdf(docx_path: Path, pdf_path: Path) -> Path:
        return blank_pdf(pdf_path, pages=2)

    import apps.cover_letter_generation.services.cover_letter_generation_service as generation

    monkeypatch.setattr(generation, "resolve_cover_letters_dir", lambda: tmp_path / "letters")
    monkeypatch.setattr(generation, "failed_root", lambda: tmp_path / "letters" / "_failed")
    monkeypatch.setattr(generation, "desktop_pdf_path", lambda: desktop)
    monkeypatch.setattr(generation, "render_cover_letter_pdf", fake_pdf)
    result = generate_cover_letter_for_job(
        session,
        job.id,
        deterministic_only=True,
        skip_company_research=True,
        copy_to_desktop=True,
    )
    assert result.status == "validation_failed"
    assert result.desktop_pdf_path is None
    assert not desktop.exists()
