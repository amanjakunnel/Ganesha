from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from apps.resume_generation.services.generation_preflight import (
    ELIGIBILITY_BLOCK_MSG,
    generation_preflight,
)
from apps.resume_generation.services.generation_service import (
    generate_resume_for_job,
    preview_generation,
)
from apps.resume_generation.services.resume_fact_service import scan_resume_corpus
from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import (
    Base,
    Company,
    JobPosting,
    ResumeFact,
    ResumeGeneration,
    ResumeSourceDocument,
    make_dedupe_key,
    make_description_hash,
    make_uuid,
)
from tests.resume_generation.conftest import build_standard_corpus


@pytest.fixture()
def session() -> Session:
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for model in (
            ResumeGeneration,
            ResumeFact,
            ResumeSourceDocument,
            JobPosting,
            Company,
        ):
            db.query(model).delete()
        db.commit()
        yield db
    finally:
        db.close()


def _job(session: Session, *, title: str, description: str, url: str = "https://x.test/a") -> JobPosting:
    company = Company(
        canonical_name=f"Co-{make_uuid()[:8]}",
        normalized_key=make_uuid()[:8],
    )
    session.add(company)
    session.flush()
    job = JobPosting(
        id=make_uuid(),
        source="test",
        title=title,
        company_id=company.id,
        location="United States",
        description_text=description,
        description_hash=make_description_hash(description),
        dedupe_key=make_dedupe_key(company.canonical_name, title, "US"),
        status="new",
        discovered_at=datetime.now(UTC),
        company_apply_url=url,
        source_import_key=f"elig|{make_uuid()}",
    )
    session.add(job)
    session.commit()
    return job


def test_clearance_job_blocks_generation(session: Session, tmp_path: Path) -> None:
    root = build_standard_corpus(tmp_path / "resumes")
    scan_resume_corpus(session, path=root, rebuild=True)
    session.commit()
    job = _job(
        session,
        title="Senior Software Infrastructure Engineer",
        description=(
            "Active and transferable U.S. government issued TS/SCI with Polygraph "
            "security clearance is required. U.S. citizenship is required. "
            "Cloud Kubernetes AWS infrastructure experience preferred. " * 5
        ),
    )
    assert generation_preflight(session, job) == ELIGIBILITY_BLOCK_MSG
    preview = preview_generation(session, job.id, deterministic_only=True)
    assert preview["can_generate"] is False
    assert preview["block_reason"] == ELIGIBILITY_BLOCK_MSG
    result = generate_resume_for_job(session, job.id, deterministic_only=True)
    session.commit()
    assert result.status == "blocked"
    assert result.error == ELIGIBILITY_BLOCK_MSG
