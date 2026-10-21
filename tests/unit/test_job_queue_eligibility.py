from __future__ import annotations

from sqlalchemy.orm import Session

from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import Base
from packages.core.services.job_intake import upsert_job_from_import
from packages.core.services.job_search_service import build_ranked_queue


def setup_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


def teardown_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)


def _insert_job(
    session: Session,
    *,
    title: str,
    description: str,
    apply_url: str | None,
    source_import_key: str,
    location: str | None = "New York, NY",
) -> str:
    job_id, _ = upsert_job_from_import(
        session,
        {
            "title": title,
            "company": "QueueTestCo",
            "description_text": description,
            "company_apply_url": apply_url,
            "location": location,
            "source_import_key": source_import_key,
        },
        source_name="simplifyjobs",
        source_type="github_sync",
    )
    session.commit()
    return job_id


def test_build_ranked_queue_defaults_to_actionable_only() -> None:
    session = SessionLocal()
    try:
        _insert_job(
            session,
            title="Actionable Role",
            description=(
                "Company: Co\nRole: Engineer\nLocation: NY\nSource: test\n"
                "Additional context to satisfy minimum description length requirements."
            ),
            apply_url="https://apply.example/1",
            source_import_key="test-actionable",
            location="New York, NY",
        )
        _insert_job(
            session,
            title="Excluded Role",
            description=(
                "Company: Co\nRole: Engineer\nLocation: NY\n"
                "Must have active security clearance and government access."
            ),
            apply_url="https://apply.example/2",
            source_import_key="test-excluded",
            location="New York, NY",
        )
        _insert_job(
            session,
            title="Canada Role",
            description=(
                "Company: Co\nRole: Engineer\nLocation: Vancouver, BC, Canada\n"
                "Source: test\nAdditional context for description length requirements."
            ),
            apply_url="https://apply.example/3",
            source_import_key="test-canada",
            location="Vancouver, BC, Canada",
        )
        actionable = build_ranked_queue(session, limit=10)
        assert len(actionable) == 1
        assert actionable[0].job.title == "Actionable Role"
    finally:
        session.close()
