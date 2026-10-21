from __future__ import annotations

from typing import Any

from apps.resume_generation.schemas import (
    ResumeDocument,
    ResumeStrategy,
    ValidationReport,
)


def build_audit_payload(
    *,
    strategy: ResumeStrategy,
    composed: ResumeDocument | None,
    validation: ValidationReport | None,
    template_version: str,
    job_description_hash: str,
) -> dict[str, Any]:
    return {
        "track": strategy.track,
        "reason": strategy.reason,
        "source_files": strategy.source_files,
        "fact_ids": composed.fact_ids if composed else strategy.selected_fact_ids,
        "gaps": strategy.gaps,
        "warnings": strategy.warnings,
        "provider": strategy.provider,
        "model": strategy.model,
        "template_version": template_version,
        "job_description_hash": job_description_hash,
        "structure": (
            {
                "experience_roles": len(composed.experience),
                "projects": len(composed.projects),
                "education": len(composed.education),
                "skill_lines": len(composed.skill_lines),
            }
            if composed
            else None
        ),
        "validation": (
            {
                "ok": validation.ok,
                "page_count": validation.page_count,
                "docx_ok": validation.docx_ok,
                "pdf_ok": validation.pdf_ok,
                "text_ok": validation.text_ok,
                "structural_ok": validation.structural_ok,
                "messages": validation.messages,
            }
            if validation
            else None
        ),
    }
