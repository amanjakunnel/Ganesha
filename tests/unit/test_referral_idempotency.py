from packages.core.domain.models import Base, DecisionRequest
from packages.core.db import get_engine, SessionLocal
from packages.core.domain.models import JobPosting
from packages.core.services.job_service import start_referral


def setup_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


def teardown_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)


def test_referral_idempotency_keys_are_assigned_and_distinct() -> None:
    session = SessionLocal()
    try:
        # create two job postings
        j1 = JobPosting(source="manual", title="Job 1", description_text="d1", description_hash="h1", dedupe_key="k1", status="new")
        j2 = JobPosting(source="manual", title="Job 2", description_text="d2", description_hash="h2", dedupe_key="k2", status="new")
        session.add_all([j1, j2])
        session.commit()
        # start referrals for both
        rt1 = start_referral(session, j1.id, cutoff_hours=1)
        rt2 = start_referral(session, j2.id, cutoff_hours=1)
        session.commit()
        # fetch decision requests
        dr1 = session.query(DecisionRequest).filter(DecisionRequest.idempotency_key == f"referral:{rt1.id}").one_or_none()
        dr2 = session.query(DecisionRequest).filter(DecisionRequest.idempotency_key == f"referral:{rt2.id}").one_or_none()
        assert dr1 is not None and dr1.idempotency_key is not None
        assert dr2 is not None and dr2.idempotency_key is not None
        assert dr1.idempotency_key != dr2.idempotency_key
    finally:
        session.close()
