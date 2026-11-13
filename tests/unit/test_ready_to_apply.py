"""Ready-to-apply selection rules and platform detection."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from packages.core.domain.models import JobPosting, make_uuid
from packages.core.services.ready_to_apply import (
    detect_ats_platform,
    is_live_posting,
    job_matches_ready_base,
    list_ready_to_apply,
    platform_counts_ready,
)


def _job(
    *,
    title: str = "Backend Engineer",
    url: str = "https://boards.greenhouse.io/acme/jobs/1",
    band: str = "recommended",
    eligibility: str = "actionable",
    description: str | None = None,
    listing_closed: bool = False,
    enriched: bool = True,
) -> JobPosting:
    desc = description or (
        "Build Python REST APIs on AWS with Docker and PostgreSQL. "
        * 25
    )
    meta: dict = {
        "eligibility": {"result": eligibility},
        "evidence_fit": {"band": band, "score": 80, "short_reason": "ok"},
        "simplify_markers": {},
    }
    if listing_closed:
        meta["simplify_markers"]["listing_closed"] = True
    if enriched:
        meta["enrichment"] = {
            "status": "enriched",
            "description_verified": True,
        }
    return JobPosting(
        id=make_uuid(),
        source="board_api",
        source_name="greenhouse",
        title=title,
        description_text=desc,
        description_hash=make_uuid(),
        dedupe_key=make_uuid(),
        status="new",
        location="Remote, US",
        company_apply_url=url,
        posted_at=datetime(2026, 11, 10, tzinfo=UTC),
        intake_metadata=meta,
    )


def test_detect_ats_platform() -> None:
    assert detect_ats_platform("https://boards.greenhouse.io/x/jobs/1") == "greenhouse"
    assert detect_ats_platform("https://jobs.lever.co/acme/abc") == "lever"
    assert detect_ats_platform("https://jobs.ashbyhq.com/acme/role") == "ashby"
    assert (
        detect_ats_platform(
            "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/Role"
        )
        == "workday"
    )
    assert detect_ats_platform("https://example.com/careers") == "other"
    assert detect_ats_platform(None) == "other"


def test_is_live_posting_respects_closed_marker() -> None:
    assert is_live_posting(_job()) is True
    assert is_live_posting(_job(listing_closed=True)) is False


def test_job_matches_ready_base_filters() -> None:
    session = SimpleNamespace()

    with (
        patch(
            "packages.core.services.ready_to_apply.resolve_job_eligibility",
            side_effect=lambda job: SimpleNamespace(
                result=(job.intake_metadata or {})
                .get("eligibility", {})
                .get("result", "actionable")
            ),
        ),
        patch(
            "packages.core.services.ready_to_apply._has_prior_or_likely_applied",
            return_value=False,
        ),
    ):
        ok, band = job_matches_ready_base(session, _job())  # type: ignore[arg-type]
        assert ok is True
        assert band == "recommended"

        ok2, _ = job_matches_ready_base(
            session, _job(band="low_fit")  # type: ignore[arg-type]
        )
        assert ok2 is False

        ok3, _ = job_matches_ready_base(
            session, _job(eligibility="excluded")  # type: ignore[arg-type]
        )
        assert ok3 is False

        ok4, _ = job_matches_ready_base(
            session, _job(listing_closed=True)  # type: ignore[arg-type]
        )
        assert ok4 is False

        stub = _job(description="short", enriched=False)
        stub.intake_metadata = {
            "eligibility": {"result": "actionable"},
            "evidence_fit": {"band": "recommended", "score": 80},
            "simplify_markers": {},
        }
        ok5, _ = job_matches_ready_base(session, stub)  # type: ignore[arg-type]
        assert ok5 is False

    mismatch = _job()
    mismatch.intake_metadata = {
        **(mismatch.intake_metadata or {}),
        "role_profile": {
            "status": "role_family_mismatch",
            "matched_family": None,
            "reason": "sales",
        },
    }
    with (
        patch(
            "packages.core.services.ready_to_apply.resolve_job_eligibility",
            return_value=SimpleNamespace(result="actionable"),
        ),
        patch(
            "packages.core.services.ready_to_apply._has_prior_or_likely_applied",
            return_value=False,
        ),
    ):
        ok6, _ = job_matches_ready_base(session, mismatch)  # type: ignore[arg-type]
        assert ok6 is False

        mismatch = _job(title="Kitchen Operations Associate")
        mismatch.intake_metadata = {
            **(mismatch.intake_metadata or {}),
            "role_profile": {"status": "role_family_mismatch"},
        }
        ok6, _ = job_matches_ready_base(session, mismatch)  # type: ignore[arg-type]
        assert ok6 is False


def test_list_ready_adapter_only_and_platform_counts() -> None:
    gh = _job(url="https://boards.greenhouse.io/acme/jobs/1")
    lever = _job(
        title="Lever Role",
        url="https://jobs.lever.co/acme/abc",
    )
    ashby = _job(
        title="Ashby Role",
        url="https://jobs.ashbyhq.com/acme/role",
    )

    class _Q:
        def __init__(self, rows: list[JobPosting]) -> None:
            self._rows = rows

        def options(self, *_a: object) -> _Q:
            return self

        def order_by(self, *_a: object) -> _Q:
            return self

        def all(self) -> list[JobPosting]:
            return self._rows

    class _S:
        def query(self, *_a: object) -> _Q:
            return _Q([gh, lever, ashby])

    with (
        patch(
            "packages.core.services.ready_to_apply.resolve_job_eligibility",
            return_value=SimpleNamespace(result="actionable"),
        ),
        patch(
            "packages.core.services.ready_to_apply._has_prior_or_likely_applied",
            return_value=False,
        ),
    ):
        items = list_ready_to_apply(_S(), adapter_only=True)  # type: ignore[arg-type]
        assert len(items) == 1
        assert items[0].platform == "greenhouse"
        assert items[0].adapter_supported is True

        counts = platform_counts_ready(_S())  # type: ignore[arg-type]
        assert counts["greenhouse"] == 1
        assert counts["lever"] == 1
        assert counts["ashby"] == 1
        assert counts["workday"] == 0
