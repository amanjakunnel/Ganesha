from pathlib import Path

from packages.core.domain.models import Base, JobPosting
from packages.core.db import get_engine, SessionLocal
from packages.core.services.job_service import import_csv, assess_job


def setup_module(module: object) -> None:
    # Create an in-memory SQLite DB for testing
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


def teardown_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)


def test_csv_import_and_dedupe(tmp_path: Path) -> None:
    session = SessionLocal()
    csv_file = tmp_path / "jobs.csv"
    csv_file.write_text("title,company,description_text\nSoftware Engineer,Acme Corp,We build APIs and backend systems\n")
    res = import_csv(session, str(csv_file))
    session.commit()
    assert res.created == 1
    # Import again -> duplicate
    res2 = import_csv(session, str(csv_file))
    session.commit()
    assert res2.duplicates == 1
    session.close()


def test_assess_job_creates_assessment() -> None:
    session = SessionLocal()
    # Create job
    jp = JobPosting(
        source="manual",
        title="Machine Learning Engineer",
        description_text="Experience with tensorflow and pytorch and model training",
        description_hash="h1",
        dedupe_key="k1",
        status="new",
    )
    session.add(jp)
    session.commit()
    job_id = jp.id
    assert job_id is not None
    a = assess_job(session, job_id)
    session.commit()
    assert a.recommended_track in ("ml", "manual_review")
    session.close()
