from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.shared import Inches, Pt, RGBColor

from apps.cover_letter_generation.schemas import CoverLetterDocument

_NAVY = RGBColor(0x1F, 0x4E, 0x79)


def render_cover_letter_docx(letter: CoverLetterDocument, output_path: Path) -> Path:
    """Single-column U.S. Letter DOCX. No tables, images, or page numbers."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    for section in document.sections:
        section.page_width = Inches(8.5)
        section.page_height = Inches(11)
        section.top_margin = Inches(0.85)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.9)
        section.right_margin = Inches(0.9)
        section.header.is_linked_to_previous = False
        section.footer.is_linked_to_previous = False
        for paragraph in section.footer.paragraphs:
            paragraph.text = ""

    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)

    name = document.add_paragraph()
    name.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name_run = name.add_run(letter.name_line)
    name_run.bold = True
    name_run.font.name = "Calibri"
    name_run.font.size = Pt(16)
    name_run.font.color.rgb = _NAVY
    name.paragraph_format.space_after = Pt(2)

    if letter.contact_line:
        contact = document.add_paragraph()
        contact.alignment = WD_ALIGN_PARAGRAPH.CENTER
        contact_run = contact.add_run(letter.contact_line)
        contact_run.font.name = "Calibri"
        contact_run.font.size = Pt(10)
        contact.paragraph_format.space_after = Pt(10)

    def add_block(text: str, *, space_before: int = 0, space_after: int = 8) -> None:
        paragraph = document.add_paragraph()
        run = paragraph.add_run(text)
        run.font.name = "Calibri"
        run.font.size = Pt(11)
        paragraph.paragraph_format.space_before = Pt(space_before)
        paragraph.paragraph_format.space_after = Pt(space_after)
        paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
        paragraph.paragraph_format.line_spacing = 1.08

    add_block(letter.date_line, space_after=10)
    add_block(letter.salutation, space_after=8)
    for paragraph_text in letter.paragraphs:
        add_block(paragraph_text, space_after=8)
    add_block(letter.signature_close, space_before=8, space_after=0)
    add_block(letter.signature_name, space_after=0)
    document.save(str(output_path))
    return output_path
