from __future__ import annotations

from apps.resume_generation.schemas import ResumeDocument
from apps.resume_generation.services.structured_document_service import trim_document

# Back-compat alias used by older tests / trim helpers
ComposedResume = ResumeDocument


def trim_for_page_budget(
    composed: ResumeDocument, *, pass_number: int
) -> ResumeDocument:
    return trim_document(composed, pass_number=pass_number)
