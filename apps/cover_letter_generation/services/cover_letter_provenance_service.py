from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from apps.cover_letter_generation.paths import TEMPLATE_VERSION
from apps.cover_letter_generation.schemas import CoverLetterPlan, CoverValidation


def plan_to_public_dict(plan: CoverLetterPlan) -> dict[str, Any]:
    def story(item: Any) -> dict[str, Any] | None:
        if item is None:
            return None
        return {
            "label": item.label,
            "kind": item.kind,
            "fact_ids": item.fact_ids,
            "overlap_terms": item.overlap_terms,
            "metric": item.metric,
        }

    return {
        "job_id": plan.job_id,
        "company": plan.company,
        "role": plan.role,
        "voice": plan.voice,
        "role_family": plan.role_family,
        "resume_track": plan.resume_track,
        "used_resume_generation": plan.used_resume_generation,
        "primary": story(plan.primary),
        "secondary": story(plan.secondary),
        "company_evidence": [
            {
                "evidence_id": item.evidence_id,
                "title": item.title,
                "source_type": item.source_type,
                "url": item.url,
                "retrieved_at": item.retrieved_at,
            }
            for item in plan.company_evidence
        ],
        "outline": plan.outline,
        "warnings": plan.warnings,
        "overlap_warnings": plan.overlap_warnings,
        "block_reason": plan.block_reason,
        "can_generate": plan.can_generate,
        "provider": plan.provider,
    }


def write_generation_metadata(
    directory: Path,
    plan: CoverLetterPlan,
    validation: CoverValidation,
    *,
    provider: str,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    metadata = {
        "template_version": TEMPLATE_VERSION,
        "provider": provider,
        **plan_to_public_dict(plan),
        "word_count": validation.word_count,
    }
    validation_payload = {
        "ok": validation.ok,
        "page_count": validation.page_count,
        "word_count": validation.word_count,
        "messages": validation.messages,
    }
    (directory / "cover_letter_metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    (directory / "cover_letter_validation.json").write_text(
        json.dumps(validation_payload, indent=2),
        encoding="utf-8",
    )
