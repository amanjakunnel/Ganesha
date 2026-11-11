from __future__ import annotations

from pathlib import Path

from apps.cover_letter_generation.services.cover_letter_composer_service import (
    compose_deterministic,
    validate_model_payload,
)
from apps.cover_letter_generation.services.cover_letter_plan_service import (
    build_cover_letter_plan,
)
from apps.resume_generation.services.resume_fact_service import scan_resume_corpus
from tests.cover_letter_generation.conftest import add_job, long_description
from tests.resume_generation.conftest import build_standard_corpus


def test_letter_does_not_invent_manager_metrics_or_authorization(session, tmp_path: Path) -> None:
    root = build_standard_corpus(tmp_path / "resumes")
    scan_resume_corpus(session, path=root, rebuild=True)
    session.commit()
    job = add_job(
        session,
        company_name="TruthCo",
        description=long_description("Do not mention visas."),
    )
    plan = build_cover_letter_plan(session, job, skip_company_research=True)
    letter = compose_deterministic(plan)
    body = "\n".join(letter.paragraphs).lower()
    assert "to whom it may concern" not in body
    assert "hiring manager" not in body
    for banned in ("visa", "sponsorship", "citizenship", "f-1", "referral", "opt"):
        assert banned not in body
    assert "secret product" not in body
    assert letter.salutation == "Dear TruthCo Hiring Team,"
    for bullet in plan.primary.source_bullets if plan.primary else []:
        if len(bullet) >= 40:
            assert bullet.lower() not in body


def test_model_output_with_unknown_metric_is_rejected(session, tmp_path: Path) -> None:
    root = build_standard_corpus(tmp_path / "resumes")
    scan_resume_corpus(session, path=root, rebuild=True)
    session.commit()
    job = add_job(session, company_name="ModelCo")
    plan = build_cover_letter_plan(session, job, skip_company_research=True)
    assert plan.primary is not None
    payload = {
        "paragraph_1": f"I am applying for the {plan.role} position at {plan.company}.",
        "paragraph_2": "I increased revenue by 99 percent in a role the evidence does not describe.",
        "paragraph_3": "I would welcome a conversation.",
        "factual_claim_ids": plan.primary.fact_ids,
        "company_fact_ids": ["not-a-real-source"],
        "warnings": [],
    }
    assert validate_model_payload(payload, plan, allowed_numbers_set=set()) is None


def test_other_company_name_does_not_leak_into_salutation(session, tmp_path: Path) -> None:
    root = build_standard_corpus(tmp_path / "resumes")
    scan_resume_corpus(session, path=root, rebuild=True)
    session.commit()
    first = add_job(session, company_name="AlphaCo")
    second = add_job(session, company_name="BetaCo")
    alpha = compose_deterministic(
        build_cover_letter_plan(session, first, skip_company_research=True)
    )
    beta = compose_deterministic(
        build_cover_letter_plan(session, second, skip_company_research=True)
    )
    assert "BetaCo" not in alpha.salutation
    assert "AlphaCo" not in beta.salutation
    assert "AlphaCo" in alpha.paragraphs[0]
    assert "BetaCo" in beta.paragraphs[0]
