"""Ready-to-apply selection rules."""

from __future__ import annotations

from unittest.mock import MagicMock

from packages.core.domain.models import JobPosting, make_uuid
from packages.core.services.evidence_fit_service import BAND_RECOMMENDED, BAND_STRETCH
from packages.core.services.job_eligibility import (
    ELIGIBILITY_ACTIONABLE,
    ELIGIBILITY_EXCLUDED,
)
from packages.core.services.ready_to_apply import (
    PLATFORM_ASHBY,
    PLATFORM_GREENHOUSE,
    PLATFORM_LEVER,
    detect_ats_platform,
    is_live_posting,
    job_is_otherwise_ready,
    list_ready_to_apply,
)


def _job(**kwargs: object) -> JobPosting:
    meta = dict(kwargs.pop("intake_metadata", {}) or {})  # type: ignore[arg-type]
    job = JobPosting(
        id=make_uuid(),
        source="board",
        source_name=str(kwargs.pop("source_name", "greenhouse")),
        title=str(kwargs.pop("title", "Software Engineer")),
        description_text=str(
            kwargs.pop(
                "description_text",
                "We need a backend engineer with Python and Kubernetes experience. "
                * 20,
            )
        ),
        description_hash=make_uuid(),
        dedupe_key=make_uuid(),
        status=str(kwargs.pop("status", "new")),
        company_apply_url=str(
            kwargs.pop(
                "company_apply_url",
                "https://boards.greenhouse.io/acme/jobs/123",
            )
        ),
        intake_metadata=meta,
    )
    for key, value in kwargs.items():
        setattr(job, key, value)
    return job


def _ready_meta(*, band: str = BAND_RECOMMENDED) -> dict:
    return {
        "eligibility": {"result": ELIGIBILITY_ACTIONABLE},
        "evidence_fit": {"band": band, "score": 80},
        "role_profile": {"status": "matched", "matched_family": "software_engineering"},
        "enrichment": {"status": "enriched", "description_verified": True},
    }


def test_detect_ats_platform_from_url() -> None:
    assert (
        detect_ats_platform(
            _job(company_apply_url="https://jobs.ashbyhq.com/acme/abc", source_name="")
        )
        == PLATFORM_ASHBY
    )
    assert (
        detect_ats_platform(
            _job(company_apply_url="https://jobs.lever.co/acme/xyz", source_name="")
        )
        == PLATFORM_LEVER
    )
    assert detect_ats_platform(_job()) == PLATFORM_GREENHOUSE


def test_closed_or_listing_closed_not_live() -> None:
    assert not is_live_posting(_job(status="closed"))
    assert not is_live_posting(
        _job(intake_metadata={"simplify_markers": {"listing_closed": True}})
    )
    assert is_live_posting(_job())


def test_job_is_otherwise_ready_requires_actionable_fit_verified(
    monkeypatch,
) -> None:
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    session.query.return_value.filter.return_value.all.return_value = []
    session.query.return_value.filter.return_value.limit.return_value.all.return_value = (
        []
    )

    monkeypatch.setattr(
        "packages.core.services.ready_to_apply.previously_applied_for_job",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "packages.core.services.ready_to_apply.resolve_job_eligibility",
        lambda job: MagicMock(result=ELIGIBILITY_ACTIONABLE),
    )
    monkeypatch.setattr(
        "packages.core.services.ready_to_apply.description_verified",
        lambda job: True,
    )

    good = _job(intake_metadata=_ready_meta())
    assert job_is_otherwise_ready(session, good) is True

    monkeypatch.setattr(
        "packages.core.services.ready_to_apply.resolve_job_eligibility",
        lambda job: MagicMock(result=ELIGIBILITY_EXCLUDED),
    )
    assert job_is_otherwise_ready(session, good) is False


def test_mismatch_and_unverified_excluded(monkeypatch) -> None:
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    session.query.return_value.filter.return_value.all.return_value = []
    session.query.return_value.filter.return_value.limit.return_value.all.return_value = (
        []
    )
    monkeypatch.setattr(
        "packages.core.services.ready_to_apply.previously_applied_for_job",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "packages.core.services.ready_to_apply.resolve_job_eligibility",
        lambda job: MagicMock(result=ELIGIBILITY_ACTIONABLE),
    )
    monkeypatch.setattr(
        "packages.core.services.ready_to_apply.description_verified",
        lambda job: True,
    )

    mismatch = _job(
        intake_metadata={
            **_ready_meta(),
            "role_profile": {"status": "role_family_mismatch"},
        }
    )
    assert job_is_otherwise_ready(session, mismatch) is False

    monkeypatch.setattr(
        "packages.core.services.ready_to_apply.description_verified",
        lambda job: False,
    )
    assert job_is_otherwise_ready(session, _job(intake_metadata=_ready_meta())) is False


def test_list_ready_to_apply_greenhouse_only_with_platform_counts(monkeypatch) -> None:
    gh = _job(
        source_name="greenhouse",
        company_apply_url="https://boards.greenhouse.io/acme/jobs/1",
        intake_metadata=_ready_meta(band=BAND_RECOMMENDED),
        title="GH Role",
    )
    ashby = _job(
        source_name="ashby",
        company_apply_url="https://jobs.ashbyhq.com/acme/1",
        intake_metadata=_ready_meta(band=BAND_STRETCH),
        title="Ashby Role",
    )
    lever = _job(
        source_name="lever",
        company_apply_url="https://jobs.lever.co/acme/1",
        intake_metadata=_ready_meta(),
        title="Lever Role",
    )
    # Extra ashby/lever so recommendation fires vs greenhouse=1
    ashby2 = _job(
        source_name="ashby",
        company_apply_url="https://jobs.ashbyhq.com/acme/2",
        intake_metadata=_ready_meta(),
        title="Ashby Role 2",
    )

    class _Q:
        def options(self, *_a):
            return self

        def order_by(self, *_a):
            return self

        def limit(self, *_a):
            return self

        def all(self):
            return [gh, ashby, lever, ashby2]

    session = MagicMock()
    session.query.return_value = _Q()
    # previously applied queries
    session.query.return_value.filter = MagicMock(
        return_value=MagicMock(
            first=MagicMock(return_value=None),
            all=MagicMock(return_value=[]),
            limit=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )
    )

    monkeypatch.setattr(
        "packages.core.services.ready_to_apply.job_is_otherwise_ready",
        lambda _s, job: True,
    )

    # Re-bind query properly for list function
    class _Session:
        def query(self, *_a):
            return _Q()

    payload = list_ready_to_apply(_Session())  # type: ignore[arg-type]
    assert payload.greenhouse_ready == 1
    assert len(payload.jobs) == 1
    assert payload.jobs[0].role == "GH Role"
    assert payload.jobs[0].prepare_path.startswith("/jobs/")
    assert payload.counts_by_platform.get("ashby") == 2
    assert payload.counts_by_platform.get("lever") == 1
    assert payload.recommendation is not None
    assert "Ashby" in payload.recommendation
