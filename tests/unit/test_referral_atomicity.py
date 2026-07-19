from typing import Any

from packages.core.domain.models import Base
from packages.core.db import get_engine, SessionLocal
from packages.core.domain.models import JobPosting, ReferralTask
from packages.core.services.job_service import start_referral
import packages.core.services.decision_service as ds
from packages.core.services.decision_service import DecisionError


def setup_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


def teardown_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)


def test_referral_creation_is_atomic_when_decision_creation_fails() -> None:
    session = SessionLocal()
    # create a job posting
    jp = JobPosting(
        source="manual",
        title="Test Job",
        description_text="desc",
        description_hash="h",
        dedupe_key="k",
        status="new",
    )
    session.add(jp)
    session.commit()
    job_id = jp.id

    # patch decision creation to raise
    original = ds.create_decision_request

    def _fail(*args: Any, **kwargs: Any) -> None:
        raise DecisionError("boom")

    ds.create_decision_request = _fail  # type: ignore[assignment]
    try:
        try:
            start_referral(session, job_id, cutoff_hours=1)
            session.commit()
            assert False, "start_referral should have raised when decision creation fails"
        except DecisionError:
            # rollback to clear failed transaction
            session.rollback()
        # ensure no referral task exists
        r = session.query(ReferralTask).filter(ReferralTask.job_posting_id == job_id).one_or_none()
        assert r is None
    finally:
        ds.create_decision_request = original
        session.close()
