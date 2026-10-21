from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from apps.resume_generation.services.resume_composer_service import compose_resume
from apps.resume_generation.services.resume_fact_service import scan_resume_corpus
from apps.resume_generation.services.resume_strategy_service import (
    build_resume_strategy,
)
from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import (
    Base,
    Company,
    JobPosting,
    ResumeFact,
    ResumeSourceDocument,
    make_dedupe_key,
    make_description_hash,
    make_uuid,
)
from tests.resume_generation.conftest import build_standard_corpus


@pytest.fixture()
def session() -> Session:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for model in (ResumeFact, ResumeSourceDocument, JobPosting, Company):
            db.query(model).delete()
        db.commit()
        yield db
    finally:
        db.close()


def test_composition_uses_only_selected_facts(session: Session, tmp_path: Path) -> None:
    root = build_standard_corpus(tmp_path / "resumes")
    scan_resume_corpus(session, path=root, rebuild=True)
    session.commit()
    company = Company(canonical_name="Comp", normalized_key="comp")
    session.add(company)
    session.flush()
    desc = "Backend Python API software engineer " * 20
    job = JobPosting(
        id=make_uuid(),
        source="test",
        title="Software Engineer",
        company_id=company.id,
        location="Remote",
        description_text=desc,
        description_hash=make_description_hash(desc),
        dedupe_key=make_dedupe_key("Comp", "Software Engineer", "Remote"),
        status="new",
        discovered_at=datetime.now(UTC),
        source_import_key=f"comp|{make_uuid()}",
    )
    session.add(job)
    session.commit()
    strategy = build_resume_strategy(session, job, deterministic_only=True)
    composed = compose_resume(session, strategy, job_title=job.title)
    assert composed.name_line
    assert composed.experience or composed.skills
    blob = " ".join(
        composed.skills + composed.experience + composed.projects + composed.education
    )
    assert "secret product" not in blob.lower()
    assert all(fid in strategy.selected_fact_ids for fid in composed.fact_ids)
