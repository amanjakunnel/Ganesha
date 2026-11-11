from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ArtifactRef:
    kind: str  # resume | cover_letter
    generation_id: str | None = None
    stamp: str | None = None
    docx_path: str | None = None
    pdf_path: str | None = None
    validation_ok: bool = False
    reusable: bool = False
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "generation_id": self.generation_id,
            "stamp": self.stamp,
            "docx_path": self.docx_path,
            "pdf_path": self.pdf_path,
            "validation_ok": self.validation_ok,
            "reusable": self.reusable,
            "note": self.note,
        }


@dataclass
class PreparePreview:
    job_id: str
    company: str
    role: str
    posting_url: str | None
    application_url: str | None
    can_prepare: bool
    block_reason: str | None
    fit_band: str | None
    fit_warning: str | None
    eligibility: str
    execution_mode: str
    resume: ArtifactRef | None
    cover_letter: ArtifactRef | None
    cover_letter_optional: bool
    include_cover_letter_default: bool
    missing: list[str] = field(default_factory=list)
    sensitive_reminders: list[str] = field(default_factory=list)
    active_attempts: list[dict[str, Any]] = field(default_factory=list)
    simplify_limits: list[str] = field(default_factory=list)
    profile_fields_available: list[str] = field(default_factory=list)
    previously_applied: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "company": self.company,
            "role": self.role,
            "posting_url": self.posting_url,
            "application_url": self.application_url,
            "can_prepare": self.can_prepare,
            "block_reason": self.block_reason,
            "fit_band": self.fit_band,
            "fit_warning": self.fit_warning,
            "eligibility": self.eligibility,
            "execution_mode": self.execution_mode,
            "resume": self.resume.to_dict() if self.resume else None,
            "cover_letter": self.cover_letter.to_dict() if self.cover_letter else None,
            "cover_letter_optional": self.cover_letter_optional,
            "include_cover_letter_default": self.include_cover_letter_default,
            "missing": self.missing,
            "sensitive_reminders": self.sensitive_reminders,
            "active_attempts": self.active_attempts,
            "simplify_limits": self.simplify_limits,
            "profile_fields_available": self.profile_fields_available,
            "previously_applied": self.previously_applied,
        }
