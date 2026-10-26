from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from apps.cover_letter_generation.services.cover_letter_composer_service import (
    compose_deterministic,
    word_count,
)
from apps.cover_letter_generation.services.cover_letter_plan_service import (
    build_cover_letter_plan,
)
from apps.resume_generation.services.resume_fact_service import scan_resume_corpus
from tests.cover_letter_generation.conftest import add_job
from tests.resume_generation.conftest import build_standard_corpus


def _plan(session, tmp_path: Path, voice: str = "technical_direct"):
    root = build_standard_corpus(tmp_path / "resumes")
    scan_resume_corpus(session, path=root, rebuild=True)
    session.commit()
    job = add_job(session, title="Software Engineer", company_name="ComposerCo")
    return build_cover_letter_plan(
        session, job, voice=voice, skip_company_research=True
    )


def test_deterministic_letter_names_role_and_company(session, tmp_path: Path) -> None:
    plan = _plan(session, tmp_path)
    letter = compose_deterministic(plan)
    body = "\n".join(letter.paragraphs)
    assert "ComposerCo" in body
    assert "Software Engineer" in body
    assert letter.salutation == "Dear ComposerCo Hiring Team,"
    assert letter.signature_name == "Alex Michael Manjakunnel"
    assert 225 <= word_count(body) <= 375
    assert len(letter.paragraphs) == 3


def test_voice_changes_tone_without_changing_employers(session, tmp_path: Path) -> None:
    plan = _plan(session, tmp_path, "technical_direct")
    direct = compose_deterministic(plan)
    product = compose_deterministic(replace(plan, voice="product_oriented"))
    assert "technical requirement" in direct.paragraphs[0].lower()
    assert "product outcome" in product.paragraphs[0].lower()
    assert "Acme Corp" in direct.paragraphs[1] or "CloudCo" in direct.paragraphs[1]
    assert direct.fact_ids == product.fact_ids


def test_no_api_key_path_is_deterministic(session, tmp_path: Path, monkeypatch) -> None:
    from packages.core.settings import settings

    monkeypatch.setattr(settings, "semantic_fit_provider", "none")
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    plan = _plan(session, tmp_path)
    letter = compose_deterministic(plan)
    assert letter.company == "ComposerCo"
    assert "excited to apply" not in " ".join(letter.paragraphs).lower()
