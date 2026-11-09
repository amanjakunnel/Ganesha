from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT

from apps.resume_generation.schemas import (
    BulletRef,
    ExperienceEntry,
    ResumeDocument,
)
from apps.resume_generation.services.docx_render_service import render_docx
from apps.resume_generation.services.experience_layout import (
    RIGHT_TAB_INCHES,
    split_employer_location,
)


def test_split_short_and_long_employer_names() -> None:
    short_co, short_loc = split_employer_location(
        "Sprinklr                  Bengaluru, India"
    )
    long_co, long_loc = split_employer_location("Tricog Health Bengaluru, India")
    assert short_co == "Sprinklr"
    assert short_loc == "Bengaluru, India"
    assert long_co == "Tricog Health"
    assert long_loc == "Bengaluru, India"
    assert "  " not in short_co


def test_render_uses_shared_tab_stops_not_space_padding(tmp_path: Path) -> None:
    doc = ResumeDocument(
        name_line="Alex Michael Manjakunnel",
        contact_line="State College, PA",
        summary=None,
        experience=[
            ExperienceEntry(
                company="Sprinklr                  Bengaluru, India",
                title="Associate Cloud Engineer",
                dates="Jul 2023 - Apr 2025",
                bullets=[
                    BulletRef(text="Built cloud environments.", fact_id="1", normalized="built")
                ],
            ),
            ExperienceEntry(
                company="Tricog Health Bengaluru, India",
                title="Machine Learning Intern",
                dates="Jul 2021 - Nov 2021",
                bullets=[
                    BulletRef(text="Built ECG pipelines.", fact_id="2", normalized="built ecg")
                ],
                location=None,
            ),
            ExperienceEntry(
                company="Very Long Employer Name International Systems",
                title="Software Engineer",
                dates="Jan 2020 - Dec 2020",
                location="San Francisco Bay Area, CA",
                bullets=[
                    BulletRef(text="Shipped services.", fact_id="3", normalized="shipped")
                ],
            ),
        ],
    )
    out = tmp_path / "layout.docx"
    render_docx(doc, out)
    rendered = Document(str(out))
    experience_rows = [
        p
        for p in rendered.paragraphs
        if p.text
        and (
            "Sprinklr" in p.text
            or "Tricog" in p.text
            or "Associate Cloud" in p.text
            or "Machine Learning" in p.text
            or "Very Long Employer" in p.text
            or "Software Engineer" in p.text
        )
    ]
    assert experience_rows
    for paragraph in experience_rows:
        assert "  " not in paragraph.text.replace("\t", "")
        stops = list(paragraph.paragraph_format.tab_stops)
        assert len(stops) == 1
        assert abs(stops[0].position.inches - RIGHT_TAB_INCHES) < 0.05
        assert stops[0].alignment == WD_TAB_ALIGNMENT.RIGHT
        # Left text then optional tab+right text; no employer-specific pad runs.
        run_texts = [r.text for r in paragraph.runs]
        assert not any(t.startswith("  ") or t.endswith("  ") for t in run_texts if t != "\t")

    # Wrapping long names still use the same tab geometry (no collision via spaces).
    long_row = next(p for p in experience_rows if "Very Long Employer" in p.text)
    assert "\t" in "".join(r.text for r in long_row.runs)
    assert "San Francisco Bay Area, CA" in long_row.text
