from __future__ import annotations

from apps.cover_letter_generation.paths import (
    ELIGIBILITY_BLOCK_MSG,
    INCOMPLETE_IDENTITY_MSG,
    INSUFFICIENT_MSG,
    MISSING_URL_MSG,
)
from apps.cover_letter_generation.services.cover_letter_preflight_service import (
    cover_letter_preflight,
)
from tests.cover_letter_generation.conftest import add_job


def test_eligible_job_passes_preflight(session) -> None:
    job = add_job(session)
    assert cover_letter_preflight(session, job) is None


def test_clearance_job_uses_safe_block_message(session) -> None:
    job = add_job(
        session,
        description="Security clearance required. " + ("Python services. " * 40),
    )
    assert cover_letter_preflight(session, job) == ELIGIBILITY_BLOCK_MSG
    assert "clearance" not in (cover_letter_preflight(session, job) or "").lower()


def test_closed_job_is_blocked(session) -> None:
    job = add_job(session, status="closed")
    assert cover_letter_preflight(session, job) == ELIGIBILITY_BLOCK_MSG


def test_short_description_is_blocked(session) -> None:
    job = add_job(session, description="Python role in a short stub.")
    assert cover_letter_preflight(session, job) == INSUFFICIENT_MSG


def test_missing_url_is_blocked(session) -> None:
    job = add_job(session, url=None)
    job.canonical_url = None
    session.commit()
    assert cover_letter_preflight(session, job) == MISSING_URL_MSG


def test_missing_company_is_blocked(session) -> None:
    job = add_job(session, company_name="Unknown")
    assert cover_letter_preflight(session, job) == INCOMPLETE_IDENTITY_MSG
