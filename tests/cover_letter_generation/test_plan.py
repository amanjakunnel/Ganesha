from __future__ import annotations

from pathlib import Path

from apps.cover_letter_generation.paths import ELIGIBILITY_BLOCK_MSG
from apps.cover_letter_generation.services.cover_letter_plan_service import (
    build_cover_letter_plan,
)
from apps.resume_generation.services.resume_fact_service import scan_resume_corpus
from tests.cover_letter_generation.conftest import add_job
from tests.resume_generation.conftest import build_standard_corpus


def _index(session, tmp_path: Path) -> None:
    root = build_standard_corpus(tmp_path / "resumes")
    scan_resume_corpus(session, path=root, rebuild=True)
    session.commit()


def test_eligible_job_has_primary_and_secondary_evidence(session, tmp_path: Path) -> None:
    _index(session, tmp_path)
    job = add_job(session, company_name="PlanCo")
    plan = build_cover_letter_plan(
        session, job, voice="technical_direct", skip_company_research=True
    )
    assert plan.block_reason is None
    assert plan.can_generate
    assert plan.primary is not None
    assert plan.primary.fact_ids
    assert plan.secondary is not None
    assert len(plan.outline) == 3
    assert "secret product" not in " ".join(plan.primary.source_bullets).lower()


def test_excluded_job_does_not_research(session) -> None:
    job = add_job(
        session,
        description="Active secret clearance required. " + ("Python. " * 80),
    )

    def transport(_url: str) -> tuple[int, str]:
        raise AssertionError("research should not run")

    plan = build_cover_letter_plan(session, job, transport=transport)
    assert plan.block_reason == ELIGIBILITY_BLOCK_MSG
    assert plan.primary is None


def test_missing_company_evidence_is_a_warning_not_a_blocker(session, tmp_path: Path) -> None:
    _index(session, tmp_path)

    def transport(_url: str) -> tuple[int, str]:
        raise ConnectionError("down")

    job = add_job(session, company_name="FallbackCo", domain="fallback.example")
    plan = build_cover_letter_plan(session, job, transport=transport)
    assert plan.can_generate
    assert plan.company_evidence == []
    assert any("job description" in warning.lower() for warning in plan.warnings)
