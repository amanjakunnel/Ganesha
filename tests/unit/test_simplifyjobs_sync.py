from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from packages.core.services.importers.simplifyjobs_github import (
    parse_simplifyjobs_readme,
    simplifyjobs_import_key,
)
from packages.core.services.job_eligibility import (
    ELIGIBILITY_ACTIONABLE,
    ELIGIBILITY_EXCLUDED,
    ELIGIBILITY_NEEDS_REVIEW,
    classify_job_eligibility,
)
from packages.core.services.simplifyjobs_sync import (
    SimplifyJobsFetchError,
    fetch_simplifyjobs_readme,
    sync_simplifyjobs,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "simplifyjobs_readme_snippet.md"


@pytest.fixture
def readme_snippet() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_parse_simplifyjobs_readme_fixture(readme_snippet: str) -> None:
    synced_at = datetime(2026, 8, 5, tzinfo=UTC)
    rows = parse_simplifyjobs_readme(readme_snippet, synced_at=synced_at)
    assert len(rows) == 5
    open_row = next(r for r in rows if r.company == "ExampleCo")
    assert (
        open_row.apply_url == "https://careers.example.com/jobs/123?utm_source=Simplify"
    )
    assert open_row.age_days == 1
    assert open_row.location == "San Jose, CA"

    closed = next(r for r in rows if r.company == "ClosedCo")
    assert closed.markers.get("listing_closed") is True
    assert closed.apply_url is None

    no_sponsor = next(r for r in rows if r.company == "NoSponsorCo")
    assert no_sponsor.markers.get("no_sponsorship") is True

    citizen = next(r for r in rows if r.company == "CitizenCo")
    assert citizen.markers.get("citizenship_required") is True

    continuation = [
        r
        for r in rows
        if r.title == "Software Engineer New Grad" and r.company == "ExampleCo"
    ]
    assert len(continuation) == 2


def test_simplifyjobs_import_key_stable() -> None:
    key1 = simplifyjobs_import_key("https://apply.example/a", "Co", "Title", "NY")
    key2 = simplifyjobs_import_key("https://apply.example/a", "Co", "Title", "NY")
    assert key1 == key2
    assert key1 != simplifyjobs_import_key(None, "Co", "Title", "NY")


def test_classify_eligibility_categories() -> None:
    excluded_clearance = classify_job_eligibility(
        title="Engineer",
        description="Must have active security clearance.",
    )
    assert excluded_clearance.result == ELIGIBILITY_EXCLUDED
    assert excluded_clearance.reason_category == "clearance_requirement"

    excluded_citizen = classify_job_eligibility(
        title="Engineer",
        description="New grad role at ExampleCo.",
        company_apply_url="https://apply.example",
        source_markers={"citizenship_required": True},
    )
    assert excluded_citizen.result == ELIGIBILITY_EXCLUDED
    assert excluded_citizen.reason_category == "citizenship_requirement"

    review_missing = classify_job_eligibility(
        title="Engineer",
        description="Short",
        company_apply_url=None,
    )
    assert review_missing.result == ELIGIBILITY_NEEDS_REVIEW

    actionable = classify_job_eligibility(
        title="Software Engineer New Grad",
        description=(
            "Company: Example\nRole: Engineer\nLocation: NY\n"
            "Source: github\nCategory: software\nAdditional context for length."
        ),
        company_apply_url="https://apply.example/jobs/1",
        location="San Jose, CA",
    )
    assert actionable.result == ELIGIBILITY_ACTIONABLE

    excluded_canada = classify_job_eligibility(
        title="Software Development Engineer - Early Career",
        description=(
            "Company: Amazon\nRole: Software Development Engineer - Early Career\n"
            "Location: Vancouver, BC, Canada\nSource: github"
        ),
        company_apply_url="https://amazon.jobs/example",
        location="Vancouver, BC, Canada",
    )
    assert excluded_canada.result == ELIGIBILITY_EXCLUDED
    assert excluded_canada.reason_category == "non_us_location"

    us_multi = classify_job_eligibility(
        title="Software Engineer – Early Career",
        description=(
            "Company: Amazon\nRole: Software Engineer\n"
            "Location: Seattle, WA Austin, TX\nSource: github extra padding text."
        ),
        company_apply_url="https://amazon.jobs/example2",
        location="9 locations Madison, WI Boston, MA Seattle, WA Austin, TX",
    )
    assert us_multi.result == ELIGIBILITY_ACTIONABLE


def test_sync_simplifyjobs_idempotent(readme_snippet: str) -> None:
    from packages.core.db import SessionLocal, get_engine
    from packages.core.domain.models import Base, JobPosting

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        first = sync_simplifyjobs(session, readme_text=readme_snippet, limit=3)
        session.commit()
        assert first.parsed == 3
        assert first.created >= 1

        count_after_first = session.query(JobPosting).count()
        second = sync_simplifyjobs(session, readme_text=readme_snippet, limit=3)
        session.commit()
        count_after_second = session.query(JobPosting).count()
        assert count_after_second == count_after_first
        assert second.duplicates >= 1
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_fetch_simplifyjobs_network_failure() -> None:
    import httpx

    with patch("packages.core.services.simplifyjobs_sync.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.side_effect = (
            httpx.ConnectError(
                "network down", request=httpx.Request("GET", "https://example.com")
            )
        )
        with pytest.raises(SimplifyJobsFetchError):
            fetch_simplifyjobs_readme()
