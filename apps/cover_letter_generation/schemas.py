from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StoryEvidence:
    kind: str
    label: str
    employer: str
    title: str
    fact_ids: list[str]
    source_bullets: list[str]
    overlap_terms: list[str]
    metric: str | None = None
    # Human-readable JD responsibility this story answers (not a keyword dump).
    jd_responsibility: str | None = None
    # strong = direct responsibility overlap; weak = transferable only.
    connection: str = "strong"


@dataclass
class CompanyEvidence:
    url: str
    title: str
    source_type: str
    retrieved_at: str
    excerpt: str
    evidence_id: str
    usable: bool = True
    published_at: str | None = None


@dataclass
class CoverLetterPlan:
    job_id: str
    company: str
    role: str
    voice: str
    role_family: str
    resume_track: str | None
    used_resume_generation: bool
    contact_line: str | None
    primary: StoryEvidence | None
    secondary: StoryEvidence | None
    company_evidence: list[CompanyEvidence] = field(default_factory=list)
    outline: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    overlap_warnings: list[str] = field(default_factory=list)
    omitted_fact_ids: list[str] = field(default_factory=list)
    block_reason: str | None = None
    provider: str = "deterministic"

    @property
    def can_generate(self) -> bool:
        return self.block_reason is None and self.primary is not None


@dataclass
class CoverLetterDocument:
    name_line: str
    contact_line: str | None
    date_line: str
    salutation: str
    paragraphs: list[str]
    company: str
    role: str
    voice: str
    fact_ids: list[str]
    company_fact_ids: list[str]
    signature_close: str = "Sincerely,"
    signature_name: str = "Alex Michael Manjakunnel"


@dataclass
class CoverValidation:
    ok: bool
    messages: list[str] = field(default_factory=list)
    page_count: int | None = None
    word_count: int = 0


@dataclass
class CoverGenerationResult:
    status: str
    job_id: str
    voice: str = "technical_direct"
    docx_path: str | None = None
    pdf_path: str | None = None
    desktop_pdf_path: str | None = None
    evidence_count: int = 0
    company_evidence_used: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    validation: CoverValidation | None = None
    plan: dict[str, Any] | None = None
    provider: str = "deterministic"
