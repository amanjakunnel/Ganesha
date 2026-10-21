from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt

from apps.resume_generation.paths import TEMPLATE_VERSION
from apps.resume_generation.schemas import ResumeDocument


def render_docx(doc: ResumeDocument, output_path: Path) -> Path:
    """Render a structured single-column ATS-safe DOCX."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = Document()

    for section in document.sections:
        section.top_margin = Inches(0.6)
        section.bottom_margin = Inches(0.6)
        section.left_margin = Inches(0.7)
        section.right_margin = Inches(0.7)

    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10)

    name = document.add_paragraph()
    name.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = name.add_run(doc.name_line)
    run.bold = True
    run.font.size = Pt(16)
    run.font.name = "Calibri"
    name.paragraph_format.space_after = Pt(2)

    if doc.contact_line:
        contact = document.add_paragraph()
        contact.alignment = WD_ALIGN_PARAGRAPH.CENTER
        c_run = contact.add_run(doc.contact_line)
        c_run.font.size = Pt(9)
        c_run.font.name = "Calibri"
        contact.paragraph_format.space_after = Pt(8)

    def add_heading(text: str) -> None:
        p = document.add_paragraph()
        r = p.add_run(text.upper())
        r.bold = True
        r.font.size = Pt(11)
        r.font.name = "Calibri"
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(2)

    def add_bullet(text: str) -> None:
        p = document.add_paragraph(style="List Bullet")
        r = p.add_run(text)
        r.font.size = Pt(10)
        r.font.name = "Calibri"
        p.paragraph_format.space_after = Pt(1)

    def add_body(text: str, *, bold: bool = False) -> None:
        p = document.add_paragraph()
        r = p.add_run(text)
        r.bold = bold
        r.font.size = Pt(10)
        r.font.name = "Calibri"
        p.paragraph_format.space_after = Pt(1)

    if doc.summary:
        add_heading("Summary")
        add_body(doc.summary)

    if doc.skill_lines:
        add_heading("Skills")
        for line in doc.skill_lines:
            add_body(line)

    if doc.experience:
        add_heading("Experience")
        for exp_entry in doc.experience:
            header = " | ".join(
                part
                for part in (exp_entry.company, exp_entry.title, exp_entry.dates)
                if part
            )
            add_body(header, bold=True)
            for bullet in exp_entry.bullets:
                add_bullet(bullet.text)

    if doc.projects:
        add_heading("Projects")
        for proj_entry in doc.projects:
            bits = [proj_entry.name]
            if proj_entry.technologies:
                bits.append(proj_entry.technologies)
            if proj_entry.date_range:
                bits.append(proj_entry.date_range)
            add_body(" | ".join(bits), bold=True)
            for bullet in proj_entry.bullets:
                add_bullet(bullet.text)

    if doc.education:
        add_heading("Education")
        for edu_entry in doc.education:
            line = " | ".join(
                part
                for part in (
                    edu_entry.institution,
                    edu_entry.degree,
                    edu_entry.major,
                    edu_entry.graduation_date,
                )
                if part
            )
            add_body(line, bold=True)
            if edu_entry.gpa:
                add_body(f"GPA: {edu_entry.gpa}/4.0")

    document.core_properties.comments = f"template:{TEMPLATE_VERSION}"
    document.save(str(output_path))
    return output_path


def extract_docx_text(path: Path) -> str:
    document = Document(str(path))
    return "\n".join(p.text for p in document.paragraphs if p.text and p.text.strip())
