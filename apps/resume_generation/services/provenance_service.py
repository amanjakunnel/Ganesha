from __future__ import annotations

from typing import Any

from apps.resume_generation.schemas import ResumeStrategy, ValidationReport
from apps.resume_generation.services.resume_composer_service import ComposedResume


def build_audit_payload(
    *,
    strategy: ResumeStrategy,
    composed: ComposedResume | None,
    validation: ValidationReport | None,
    template_version: str,
    job_description_hash: str,
) -> dict[str, Any]:
    """Non-secret audit summary suitable for DB storage."""
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
        "validation": (
            {
                "ok": validation.ok,
                "page_count": validation.page_count,
                "docx_ok": validation.docx_ok,
                "pdf_ok": validation.pdf_ok,
                "text_ok": validation.text_ok,
                "messages": validation.messages,
            }
            if validation
            else None
        ),
    }
