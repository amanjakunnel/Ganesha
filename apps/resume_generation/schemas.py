from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractedFact:
    section: str
    source_text: str
    normalized_text: str
    content_hash: str
    employer_or_project: str | None = None
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


@dataclass
class ValidationReport:
    ok: bool
    page_count: int | None
    docx_ok: bool
    pdf_ok: bool
    text_ok: bool
    messages: list[str] = field(default_factory=list)


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
