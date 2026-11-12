from __future__ import annotations

from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import (
    Base,
    Company,
    JobPosting,
    make_dedupe_key,
    make_description_hash,
    make_uuid,
)
from packages.core.services.job_eligibility import (
    ELIGIBILITY_EXCLUDED,
    ELIGIBILITY_NEEDS_REVIEW,
    is_stub_description,
    resolve_job_eligibility,
)
from packages.core.services.job_enrichment import (
    EnrichmentHttpClient,
    enrich_job,
    fetch_job_content,
    greenhouse_api_job_url,
    workday_cxs_url,
)


NVIDIA_STUB = (
    "Company: NVIDIA\n"
    "Role: Research Scientist New Grad - ML Systems\n"
    "Location: 4 locations Seattle, WA Austin, TX Santa Clara, CA Westford, MA\n"
    "Category: Data Science, AI & Machine Learning New Grad Roles\n"
    "Source: https://github.com/SimplifyJobs/New-Grad-Positions"
)

NVIDIA_LIVE_TITLE = "Research Scientist, ML Systems - PhD New College Grad 2026"
NVIDIA_LIVE_HTML = """<!DOCTYPE html><html><head>
<meta property="og:title" content="Research Scientist, ML Systems - PhD New College Grad 2026">
<meta property="og:description" content="Today, NVIDIA is tapping into the unlimited potential of AI.">
<script>window.__x={"title":"Research Scientist, ML Systems - PhD New College Grad 2026"}</script>
</head><body></body></html>"""

NVIDIA_CXS = {
    "jobPostingInfo": {
        "id": "abc",
        "title": NVIDIA_LIVE_TITLE,
        "jobDescription": (
            "<p>Today, NVIDIA is tapping into AI.</p>"
            "<p>We are looking for PhD students and graduates for ML systems research.</p>"
            "<p>United States locations. Full-time new college graduate role.</p>"
        ),
    }
}


def _session() -> Session:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def _make_job(
    session: Session,
    *,
    title: str,
    description: str,
    apply_url: str,
    meta: dict | None = None,
) -> JobPosting:
    company = Company(canonical_name=f"Co-{make_uuid()[:8]}", normalized_key=make_uuid())
    session.add(company)
    session.flush()
    job = JobPosting(
        id=make_uuid(),
        source="simplifyjobs",
        source_name="simplifyjobs",
        source_type="github_sync",
        title=title,
        company_id=company.id,
        location="Santa Clara, CA",
        description_text=description,
        description_hash=make_description_hash(description),
        dedupe_key=make_dedupe_key("NVIDIA", title, "Santa Clara, CA"),
        status="new",
        discovered_at=datetime.now(UTC),
        company_apply_url=apply_url,
        canonical_url=apply_url,
        source_import_key=f"enrich|{make_uuid()}",
        intake_metadata=meta or {"simplify_markers": {}},
    )
    session.add(job)
    session.commit()
    return job


def test_stub_detection_simplify_aggregator() -> None:
    assert is_stub_description(NVIDIA_STUB)
    assert not is_stub_description(
        "A" * 80
        + " Full job description with responsibilities and requirements for engineers."
    )


def test_workday_cxs_url_derivation() -> None:
    url = (
        "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/"
        "US-CA-Santa-Clara/Research-Scientist--ML-Systems---New-College-Grad-2026_JR2010161"
        "?utm_source=Simplify"
    )
    cxs = workday_cxs_url(url)
    assert cxs is not None
    assert "/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/" in cxs
    assert cxs.endswith("JR2010161")


def test_greenhouse_api_url() -> None:
    assert (
        greenhouse_api_job_url("https://boards.greenhouse.io/vaulttec/jobs/127817")
        == "https://boards-api.greenhouse.io/v1/boards/vaulttec/jobs/127817"
    )


def test_nvidia_like_enrichment_title_drift_and_phd_exclusion() -> None:
    apply = (
        "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/"
        "US-CA-Santa-Clara/Research-Scientist--ML-Systems---New-College-Grad-2026_JR2010161"
    )
    cxs = workday_cxs_url(apply)
    assert cxs

    def handler(request: httpx.Request) -> httpx.Response:
        if "wday/cxs" in str(request.url):
            return httpx.Response(200, json=NVIDIA_CXS)
        return httpx.Response(200, text=NVIDIA_LIVE_HTML)

    transport = httpx.MockTransport(handler)
    session = _session()
    try:
        job = _make_job(
            session,
            title="Research Scientist New Grad - ML Systems",
            description=NVIDIA_STUB,
            apply_url=apply,
        )
        before = resolve_job_eligibility(job)
        assert before.result == ELIGIBILITY_NEEDS_REVIEW
        assert before.reason_category == "description_unverified"

        with EnrichmentHttpClient(transport=transport, min_interval=0) as client:
            outcome = enrich_job(session, job, client=client, dry_run=False)
        session.commit()
        session.refresh(job)

        assert outcome.status == "enriched"
        assert outcome.title_updated is True
        assert outcome.previous_title == "Research Scientist New Grad - ML Systems"
        assert job.title == NVIDIA_LIVE_TITLE
        assert not is_stub_description(job.description_text)
        assert (job.intake_metadata or {}).get("enrichment", {}).get("status") == "enriched"
        after = resolve_job_eligibility(job)
        assert after.result == ELIGIBILITY_EXCLUDED
        assert after.reason_category == "phd_required"
    finally:
        session.close()


def test_enrichment_unchanged_hash_skips_rewrite() -> None:
    apply = "https://boards.greenhouse.io/vaulttec/jobs/127817"
    payload = {
        "id": 127817,
        "title": "Software Engineer New Grad",
        "content": "<p>" + ("Build reliable systems. " * 20) + "</p>",
    }
    content_hash = make_description_hash(
        "Build reliable systems. " * 20
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, headers={"ETag": "abc"})

    transport = httpx.MockTransport(handler)
    session = _session()
    try:
        long_desc = ("Build reliable systems. " * 20).strip()
        job = _make_job(
            session,
            title="Software Engineer New Grad",
            description=NVIDIA_STUB,
            apply_url=apply,
            meta={
                "simplify_markers": {},
                "enrichment": {
                    "status": "enriched",
                    "content_hash": make_description_hash(long_desc),
                    "description_verified": True,
                },
            },
        )
        # Force path: description still stub so needs enrichment; after first enrich, second is unchanged.
        with EnrichmentHttpClient(transport=transport, min_interval=0) as client:
            first = enrich_job(session, job, client=client, force=True)
            session.commit()
            session.refresh(job)
            second = enrich_job(session, job, client=client, force=True)
        assert first.status == "enriched"
        assert second.status == "unchanged"
        assert content_hash  # sanity
    finally:
        session.close()


def test_failed_enrichment_marks_unverified_not_excluded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    transport = httpx.MockTransport(handler)
    session = _session()
    try:
        job = _make_job(
            session,
            title="Software Engineer New Grad",
            description=NVIDIA_STUB,
            apply_url="https://example.com/jobs/1",
        )
        with EnrichmentHttpClient(transport=transport, min_interval=0) as client:
            outcome = enrich_job(session, job, client=client)
        session.commit()
        session.refresh(job)
        assert outcome.status in {"failed", "unavailable"}
        assert outcome.description_verified is False
        elig = resolve_job_eligibility(job)
        assert elig.result == ELIGIBILITY_NEEDS_REVIEW
        assert elig.reason_category == "description_unverified"
        assert elig.result != ELIGIBILITY_EXCLUDED
    finally:
        session.close()


def test_fetch_greenhouse_content() -> None:
    payload = {
        "id": 1,
        "title": "New Grad Engineer",
        "content": "<p>Ship product features across the stack in the United States.</p>" * 5,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    with EnrichmentHttpClient(transport=transport, min_interval=0) as client:
        fetched = fetch_job_content(
            client, "https://boards.greenhouse.io/acme/jobs/1"
        )
    assert fetched.provider == "greenhouse"
    assert fetched.title == "New Grad Engineer"
    assert fetched.description and "Ship product" in fetched.description
