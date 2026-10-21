"""Shared constants and path helpers for resume generation."""

from __future__ import annotations

from pathlib import Path

from packages.core.settings import settings

TEMPLATE_VERSION = "ganesha-ats-v1"
OUTPUT_BASENAME = "Alex_Michael_Manjakunnel_Resume"
REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_ROOT = REPO_ROOT / "data" / "generated_resumes"
FAILED_ROOT = GENERATED_ROOT / "_failed"

PRIMARY_TRACK_FILES = {
    "Current_Resume_Dev.docx": "dev",
    "Current_Resume_Cloud.docx": "cloud",
    "Current_Resume_ML.docx": "ml",
    "Current_Resume_C#.docx": "csharp",
    "Current_Resume.docx": "general",
}

SECTION_ALIASES = {
    "education": "education",
    "skills": "skills",
    "skills & publication": "skills",
    "skills and publication": "skills",
    "technical skills": "skills",
    "work experience": "experience",
    "experience": "experience",
    "professional experience": "experience",
    "projects": "projects",
    "project": "projects",
    "publication": "publications",
    "publications": "publications",
    "certifications": "certifications",
    "leadership": "leadership",
}


def default_source_path() -> Path:
    return Path(settings.resume_source_path)


def desktop_pdf_path() -> Path:
    return Path(settings.resume_desktop_pdf_path)


def classify_track(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = Path(path.name)
    parts = [p.lower() for p in rel.parts]
    if "company specific" in parts or "company_specific" in parts:
        return "company_specific_reference"
    return PRIMARY_TRACK_FILES.get(path.name, "general")
