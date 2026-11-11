"""Shared experience-row layout for ATS resumes (no space-padding alignment)."""

from __future__ import annotations

import re

from docx.document import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.shared import Inches, Pt
from docx.text.paragraph import Paragraph

# Usable content width with 0.7" left/right margins on US Letter.
CONTENT_WIDTH_INCHES = 7.1
RIGHT_TAB_INCHES = CONTENT_WIDTH_INCHES

_MULTI_SPACE = re.compile(r"^(?P<company>.+?)\s{2,}(?P<location>.+)$")
# Greedy company so "Tricog Health Bengaluru, India" keeps "Tricog Health" intact.
_TRAILING_CITY_REGION = re.compile(
    r"^(?P<company>.+)\s+(?P<location>"
    r"[A-Za-z][A-Za-z .'-]*,\s*"
    r"(?:India|United States|USA|U\.S\.A\.|Canada|United Kingdom|UK|U\.K\.|"
    r"Germany|France|Singapore|Australia|[A-Z]{2})"
    r")$"
)


def split_employer_location(raw: str | None) -> tuple[str, str | None]:
    """Split employer name from a right-aligned location without keeping pad spaces."""
    text = (raw or "").replace("\t", "  ").strip()
    if not text:
        return "", None
    match = _MULTI_SPACE.match(text)
    if match:
        return match.group("company").strip(), match.group("location").strip()
    match = _TRAILING_CITY_REGION.match(text)
    if match:
        return match.group("company").strip(), match.group("location").strip()
    return re.sub(r"\s+", " ", text), None


def configure_left_right_tab(paragraph: Paragraph, *, right_tab_inches: float = RIGHT_TAB_INCHES) -> None:
    """Apply a single right-aligned tab stop used by all equivalent experience rows."""
    pf = paragraph.paragraph_format
    # Clear inherited tab stops so every row shares one rule.
    pf.tab_stops.clear_all()
    pf.tab_stops.add_tab_stop(
        Inches(right_tab_inches),
        WD_TAB_ALIGNMENT.RIGHT,
        WD_TAB_LEADER.SPACES,
    )
    pf.space_before = Pt(0)
    pf.space_after = Pt(1)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT


def add_left_right_row(
    document: Document,
    left: str,
    right: str | None,
    *,
    bold: bool = False,
    font_size_pt: float = 10,
    font_name: str = "Calibri",
    right_tab_inches: float = RIGHT_TAB_INCHES,
) -> Paragraph:
    """Add a single-column left/right row using a tab stop (never space padding)."""
    paragraph = document.add_paragraph()
    configure_left_right_tab(paragraph, right_tab_inches=right_tab_inches)
    left_run = paragraph.add_run((left or "").strip())
    left_run.bold = bold
    left_run.font.size = Pt(font_size_pt)
    left_run.font.name = font_name
    if right and right.strip():
        paragraph.add_run("\t")
        right_run = paragraph.add_run(right.strip())
        right_run.bold = bold
        right_run.font.size = Pt(font_size_pt)
        right_run.font.name = font_name
    return paragraph
