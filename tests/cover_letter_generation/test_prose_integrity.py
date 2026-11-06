from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from apps.cover_letter_generation.schemas import CoverLetterPlan, StoryEvidence
from apps.cover_letter_generation.services.cover_letter_composer_service import (
    compose_deterministic,
    prose_problems,
)
from apps.cover_letter_generation.services.cover_letter_plan_service import (
    _complete_metric_from_bullet,
    _metric_for_story,
    build_cover_letter_plan,
)
from apps.resume_generation.services.resume_fact_service import scan_resume_corpus
from tests.cover_letter_generation.conftest import add_job
from tests.resume_generation.conftest import build_standard_corpus


_LEAKAGE = (
    "verified work",
    "verified evidence",
    "outside the record",
    "claim bounded",
    "adjacent to",
    "partially covers",
    "recorded outcome",
    "below i connect",
    "staying inside",
)


def _plan(session: object, tmp_path: Path) -> CoverLetterPlan:
    root = build_standard_corpus(tmp_path / "resumes")
    scan_resume_corpus(session, path=root, rebuild=True)  # type: ignore[arg-type]
    session.commit()  # type: ignore[attr-defined]
    job = add_job(session, title="Software Engineer", company_name="IntegrityCo")  # type: ignore[arg-type]
    return build_cover_letter_plan(session, job, skip_company_research=True)  # type: ignore[arg-type]


def test_letter_has_no_internal_process_leakage(session: object, tmp_path: Path) -> None:
    letter = compose_deterministic(_plan(session, tmp_path))
    body = " ".join(letter.paragraphs).lower()
    for phrase in _LEAKAGE:
        assert phrase not in body, phrase


def test_metric_requires_before_after_on_same_bullet() -> None:
    kafka = (
        "Built Python and Ansible alerting pipelines for Kafka workflows, "
        "automating health checks and notifications."
    )
    cicd = (
        "Debugged CI/CD scripts, cutting pipeline time from roughly 3.5 hours "
        "to under 1 hour."
    )
    # Sibling CI/CD metric must not attach to a Kafka-first story.
    assert _metric_for_story([kafka, cicd]) is None
    assert _complete_metric_from_bullet(kafka) is None
    assert _complete_metric_from_bullet(cicd) == "from 3.5 hours to 1 hour"
    assert _metric_for_story([cicd, kafka]) == "from 3.5 hours to 1 hour"


def test_orphan_metric_in_prose_is_rejected() -> None:
    story = StoryEvidence(
        kind="experience",
        label="DevOps Intern at Sprinklr",
        employer="Sprinklr",
        title="DevOps Intern",
        fact_ids=["f1"],
        source_bullets=[
            "Built Python and Ansible alerting pipelines for Kafka workflows."
        ],
        overlap_terms=["python"],
        metric="3.5 hours",  # orphan — should never be narrated
        jd_responsibility="Investigate production issues",
        connection="weak",
    )
    plan = CoverLetterPlan(
        job_id="job",
        company="IntegrityCo",
        role="Software Engineer",
        voice="technical_direct",
        contact_line="a@example.com",
        role_family="cloud",
        resume_track="cloud",
        used_resume_generation=False,
        primary=story,
        secondary=None,
        company_evidence=[],
        outline=[],
        warnings=[],
        overlap_warnings=[],
        block_reason=None,
        provider="deterministic",
    )
    letter = compose_deterministic(plan)
    body = " ".join(letter.paragraphs).lower()
    assert "3.5" not in body
    assert "recorded outcome" not in body


def test_cross_accomplishment_metric_not_narrated() -> None:
    story = StoryEvidence(
        kind="experience",
        label="DevOps Intern at Sprinklr",
        employer="Sprinklr",
        title="DevOps Intern",
        fact_ids=["f1", "f2"],
        source_bullets=[
            "Built Python and Ansible alerting pipelines for Kafka workflows.",
            "Cut pipeline time from roughly 3.5 hours to under 1 hour.",
        ],
        overlap_terms=["python"],
        # Planner mistake simulation: metric from sibling bullet.
        metric="from 3.5 hours to 1 hour",
        jd_responsibility="Investigate production issues",
        connection="weak",
    )
    plan = CoverLetterPlan(
        job_id="job",
        company="IntegrityCo",
        role="Software Engineer",
        voice="technical_direct",
        contact_line="a@example.com",
        role_family="cloud",
        resume_track="cloud",
        used_resume_generation=False,
        primary=story,
        secondary=None,
        company_evidence=[],
        outline=[],
        warnings=[],
        overlap_warnings=[],
        block_reason=None,
        provider="deterministic",
    )
    letter = compose_deterministic(plan)
    body = " ".join(letter.paragraphs).lower()
    # Narrated bullet is Kafka; CI/CD metric must stay out.
    assert "3.5" not in body
    assert "kafka" in body or "ansible" in body


def test_pasted_requirement_fragment_fails_quality() -> None:
    story = StoryEvidence(
        kind="experience",
        label="Engineer at Acme",
        employer="Acme",
        title="Engineer",
        fact_ids=["f1"],
        source_bullets=["Built staging and production environments across AWS and Azure."],
        overlap_terms=["aws"],
        jd_responsibility="Own end-to-end backend data flows from source system",
        connection="strong",
    )
    plan = CoverLetterPlan(
        job_id="job",
        company="IntegrityCo",
        role="Software Engineer",
        voice="technical_direct",
        contact_line="a@example.com",
        role_family="cloud",
        resume_track="cloud",
        used_resume_generation=False,
        primary=story,
        secondary=None,
        company_evidence=[],
        outline=[],
        warnings=[],
        overlap_warnings=[],
        block_reason=None,
        provider="deterministic",
    )
    # Inject a pasted fragment into an otherwise valid letter.
    letter = compose_deterministic(plan)
    bad = replace(
        letter,
        paragraphs=[
            letter.paragraphs[0],
            letter.paragraphs[1]
            + " Own end-to-end backend data flows from source system is the ask.",
            letter.paragraphs[2],
        ],
    )
    problems = prose_problems(
        bad,
        plan,
        allowed_numbers=set(),
    )
    assert any("job-description" in p.lower() for p in problems)
