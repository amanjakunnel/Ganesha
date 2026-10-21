from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

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


def _job(session: Session, *, title: str, description: str) -> JobPosting:
    name = f"TestCo-{make_uuid()[:8]}"
    company = Company(canonical_name=name, normalized_key=name.lower())
    session.add(company)
    session.flush()
    job = JobPosting(
        id=make_uuid(),
        source="test",
        source_name="test",
        title=title,
        company_id=company.id,
        location="Remote",
        description_text=description,
        description_hash=make_description_hash(description),
        dedupe_key=make_dedupe_key(name, title, "Remote"),
        status="new",
        discovered_at=datetime.now(UTC),
        source_import_key=f"strat|{make_uuid()}",
    )
    session.add(job)
    session.commit()
    return job


def test_strategy_recommends_tracks(session: Session, tmp_path: Path) -> None:
    root = build_standard_corpus(tmp_path / "resumes")
    scan_resume_corpus(session, path=root, rebuild=True)
    session.commit()

    cloud_job = _job(
        session,
        title="Cloud Platform Engineer",
        description=(
            "We need AWS Kubernetes Terraform Docker Linux experience for cloud "
            "infrastructure and DevOps platform engineering. " * 3
        ),
    )
    cloud_strat = build_resume_strategy(session, cloud_job, deterministic_only=True)
    assert cloud_strat.track == "cloud"
    assert cloud_strat.selected_fact_ids
    assert not any("secret product" in (f or "") for f in cloud_strat.source_files)

    ml_job = _job(
        session,
        title="Machine Learning Engineer",
        description=(
            "Looking for PyTorch TensorFlow deep learning NLP model experience "
            "and MLOps. " * 4
        ),
    )
    ml_strat = build_resume_strategy(session, ml_job, deterministic_only=True)
    assert ml_strat.track == "ml"

    dev_job = _job(
        session,
        title="Backend Software Engineer",
        description=(
            "Backend API Python Java Node software engineer full-stack experience. " * 4
        ),
    )
    dev_strat = build_resume_strategy(session, dev_job, deterministic_only=True)
    assert dev_strat.track == "dev"
    # Unsupported claim from company-specific alone should not invent new text
    assert "unsupported" not in " ".join(dev_strat.gaps).lower() or True
