from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractedFact:
    section: str
    source_text: str
    normalized_text: str
    content_hash: str
    fact_kind: str = "bullet"
    employer_or_project: str | None = None
    parent_entity_type: str | None = None
    parent_name: str | None = None
    role_title: str | None = None
    parent_key: str | None = None
    gpa_value: str | None = None
    skills: list[str] = field(default_factory=list)
    dates_text: str | None = None
    metrics: list[str] = field(default_factory=list)


@dataclass
class ScanSummary:
    discovered: int = 0
    parsed: int = 0
    skipped: int = 0
    changed: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    duplicate_facts: int = 0
    conflict_warnings: int = 0


@dataclass
class RequirementSet:
    keywords: list[str]
    role_family: str
    insufficient_description: bool = False


@dataclass
class BulletRef:
    text: str
    fact_id: str
    normalized: str


@dataclass
class ExperienceEntry:
    company: str
    title: str
    dates: str
    bullets: list[BulletRef] = field(default_factory=list)
    parent_key: str = ""
    location: str | None = None


@dataclass
class ProjectEntry:
    name: str
    bullets: list[BulletRef] = field(default_factory=list)
    technologies: str | None = None
    date_range: str | None = None
    parent_key: str = ""


@dataclass
class EducationEntry:
    institution: str
    degree: str
    major: str | None = None
    graduation_date: str | None = None
    gpa: str | None = None
    coursework: str | None = None
    parent_key: str = ""
    fact_ids: list[str] = field(default_factory=list)


@dataclass
class ResumeDocument:
    name_line: str
    contact_line: str | None
    summary: str | None
    skill_lines: list[str] = field(default_factory=list)
    experience: list[ExperienceEntry] = field(default_factory=list)
    projects: list[ProjectEntry] = field(default_factory=list)
    education: list[EducationEntry] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    omit_reasons: list[str] = field(default_factory=list)
    track: str = "general"


@dataclass
class ResumeStrategy:
    track: str
    reason: str
    selected_fact_ids: list[str]
    source_files: list[str]
    outline: dict[str, Any]
    gaps: list[str]
    warnings: list[str]
    provider: str = "deterministic"
    model: str | None = None
    document: ResumeDocument | None = None
    block_reason: str | None = None


@dataclass
class ValidationReport:
    ok: bool
    page_count: int | None
    docx_ok: bool
    pdf_ok: bool
    text_ok: bool
    messages: list[str] = field(default_factory=list)
    structural_ok: bool = False


@dataclass
class GenerationResult:
    status: str
    generation_id: str | None = None
    track: str | None = None
    docx_path: str | None = None
    pdf_path: str | None = None
    desktop_pdf_path: str | None = None
    source_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    validation: ValidationReport | None = None
    error: str | None = None
    provider: str = "deterministic"
