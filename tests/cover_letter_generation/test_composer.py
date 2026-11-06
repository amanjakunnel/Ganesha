from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from apps.cover_letter_generation.services.cover_letter_composer_service import (
    compose_deterministic,
    prose_problems,
    style_warnings,
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
    assert 100 <= word_count(body) <= 350
    assert len(letter.paragraphs) == 3
    from apps.cover_letter_generation.services.cover_letter_composer_service import (
        allowed_numbers,
    )

    nums = allowed_numbers(plan, "Software Engineer Python AWS Docker")
    assert prose_problems(letter, plan, allowed_numbers=nums) == []


def test_voice_changes_tone_without_changing_employers(session, tmp_path: Path) -> None:
    plan = _plan(session, tmp_path, "technical_direct")
    direct = compose_deterministic(plan)
    product = compose_deterministic(replace(plan, voice="product_oriented"))
    assert "product" in product.paragraphs[0].lower() or "reliable" in product.paragraphs[0].lower()
    assert direct.paragraphs[0] != product.paragraphs[0]
    assert "Acme Corp" in direct.paragraphs[1] or "CloudCo" in direct.paragraphs[1]
    assert direct.fact_ids == product.fact_ids


def test_no_api_key_path_is_deterministic(session, tmp_path: Path, monkeypatch) -> None:
    from packages.core.settings import settings

    monkeypatch.setattr(settings, "semantic_fit_provider", "none")
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    plan = _plan(session, tmp_path)
    letter = compose_deterministic(plan)
    assert letter.company == "ComposerCo"
    body = " ".join(letter.paragraphs).lower()
    assert "excited to apply" not in body
    assert "concrete fit" not in body
    assert "the verified work involved" not in body
    assert "the strongest related example is" not in body
    assert "the posting emphasizes" not in body
    assert "i am writing about the" not in body
    # Must not dump raw matcher tokens as the relevance story.
    assert "emphasizes aws" not in body
    assert "emphasizes python" not in body


def test_letter_uses_responsibility_not_keyword_dump(session, tmp_path: Path) -> None:
    plan = _plan(session, tmp_path)
    assert plan.primary is not None
    letter = compose_deterministic(plan)
    body = "\n".join(letter.paragraphs)
    lower = body.lower()
    assert "aws and azure" not in lower
    assert "the posting emphasizes" not in lower
    assert "posting ask" not in lower
    assert "asks for this:" not in lower
    assert "i am writing about the" not in lower
    assert "verified work" not in lower
    assert "adjacent to" not in lower
    # Accomplishment-forward: employer or title should appear.
    assert "acme" in lower or "cloudco" in lower or "intern" in lower
    if "aws" in body.lower():
        assert "AWS" in body


def test_letter_adds_context_beyond_resume_bullets(session, tmp_path: Path) -> None:
    plan = _plan(session, tmp_path)
    letter = compose_deterministic(plan)
    body = "\n".join(letter.paragraphs)
    assert "ComposerCo" in body
    assert (
        "bring" in body.lower()
        or "transfer" in body.lower()
        or "apply" in body.lower()
        or "contribute" in body.lower()
        or "supports" in body.lower()
    )
    for bullet in plan.primary.source_bullets if plan.primary else []:
        cleaned = " ".join(bullet.split())
        if len(cleaned) >= 40:
            assert cleaned.lower() not in body.lower()


def test_style_warnings_do_not_block_good_writing(session, tmp_path: Path) -> None:
    plan = _plan(session, tmp_path)
    letter = compose_deterministic(plan)
    from apps.cover_letter_generation.services.cover_letter_composer_service import (
        allowed_numbers,
    )

    nums = allowed_numbers(plan, plan.role)
    assert prose_problems(letter, plan, allowed_numbers=nums) == []
    # Style warnings may be empty for clean letters; they must not become problems.
    _ = style_warnings(letter)


def test_two_jobs_do_not_share_identical_template_paragraphs(
    session, tmp_path: Path
) -> None:
    root = build_standard_corpus(tmp_path / "resumes")
    scan_resume_corpus(session, path=root, rebuild=True)
    session.commit()
    first = add_job(session, title="Backend Engineer", company_name="AlphaSystems")
    second = add_job(session, title="Platform Engineer", company_name="BetaSystems")
    a = compose_deterministic(
        build_cover_letter_plan(session, first, skip_company_research=True)
    )
    b = compose_deterministic(
        build_cover_letter_plan(session, second, skip_company_research=True)
    )
    assert a.paragraphs[0] != b.paragraphs[0]
    assert "AlphaSystems" in a.paragraphs[0]
    assert "BetaSystems" in b.paragraphs[0]
