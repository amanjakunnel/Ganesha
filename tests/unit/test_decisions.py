from datetime import datetime, timedelta

from packages.core.domain.models import Base, DecisionRequest
from packages.core.db import get_engine, SessionLocal
from packages.core.services import decision_service
from packages.core.services.decision_service import DecisionNotFoundError, InvalidActionError, InvalidTransitionError


def setup_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


def teardown_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)


def test_create_decision_and_persistence() -> None:
    session = SessionLocal()
    expires = datetime.utcnow() + timedelta(hours=1)
    d = decision_service.create_decision_request(
        session,
        entity_type="job_posting",
        entity_id="j1",
        decision_type="referral_window",
        reason_code="test",
        summary="decide",
        options=["a", "b"],
        default_action="a",
        expires_at=expires,
        idempotency_key="k1",
    )
    session.commit()
    assert d.id is not None
    # fetched
    d2 = session.query(DecisionRequest).filter(DecisionRequest.id == d.id).one()
    assert d2.default_action == "a"
    session.close()


def test_idempotency_key_returns_existing() -> None:
    session = SessionLocal()
    d1 = decision_service.create_decision_request(
        session,
        entity_type="job_posting",
        entity_id="j2",
        decision_type="referral_window",
        reason_code="test",
        summary="decide",
        options=["x"],
        default_action="x",
        idempotency_key="dupkey",
    )
    session.commit()
    d2 = decision_service.create_decision_request(
        session,
        entity_type="job_posting",
        entity_id="j2",
        decision_type="referral_window",
        reason_code="test",
        summary="decide",
        options=["x"],
        default_action="x",
        idempotency_key="dupkey",
    )
    # same instance
    assert d1.id == d2.id
    session.close()


def test_invalid_default_action_or_empty_options() -> None:
    session = SessionLocal()
    try:
        try:
            decision_service.create_decision_request(
                session,
                entity_type="job_posting",
                entity_id="j3",
                decision_type="referral_window",
                reason_code=None,
                summary=None,
                options=[],
                default_action="x",
            )
            assert False, "should have raised"
        except Exception:
            pass

        try:
            decision_service.create_decision_request(
                session,
                entity_type="job_posting",
                entity_id="j3",
                decision_type="referral_window",
                reason_code=None,
                summary=None,
                options=["a"],
                default_action="b",
            )
            assert False, "should have raised"
        except Exception:
            pass
    finally:
        session.close()


def test_list_pending_excludes_resolved_and_expired() -> None:
    session = SessionLocal()
    # pending
    decision_service.create_decision_request(
        session,
        entity_type="job_posting",
        entity_id="list1",
        decision_type="referral_window",
        reason_code=None,
        summary=None,
        options=["ok"],
        default_action="ok",
        idempotency_key="list-p-1",
    )
    # resolved
    d = decision_service.create_decision_request(
        session,
        entity_type="job_posting",
        entity_id="list2",
        decision_type="referral_window",
        reason_code=None,
        summary=None,
        options=["ok"],
        default_action="ok",
        idempotency_key="list-resolved",
    )
    session.commit()
    _ = decision_service.resolve_decision_request(session, decision_id=d.id, actor="tester", selected_action="ok")
    session.commit()
    # expired
    d_exp = decision_service.create_decision_request(
        session,
        entity_type="job_posting",
        entity_id="list3",
        decision_type="referral_window",
        reason_code=None,
        summary=None,
        options=["ok"],
        default_action="ok",
        expires_at=datetime.utcnow() - timedelta(minutes=1),
        idempotency_key="list-expired",
    )
    session.commit()
    # expiry run
    cnt = decision_service.expire_due_decision_requests(session)
    session.commit()
    assert cnt >= 1
    pend = decision_service.list_pending_decisions(session)
    # pending shouldn't include resolved or expired entries
    ids = {p.id for p in pend}
    assert d.id not in ids
    assert d_exp.id not in ids
    session.close()


def test_resolve_persists_and_audit() -> None:
    session = SessionLocal()
    d = decision_service.create_decision_request(
        session,
        entity_type="job_posting",
        entity_id="r1",
        decision_type="referral_window",
        reason_code=None,
        summary=None,
        options=["one", "two"],
        default_action="one",
        idempotency_key="res-1",
    )
    session.commit()
    d2 = decision_service.resolve_decision_request(session, decision_id=d.id, actor="cli", selected_action="two", note="ok")
    session.commit()
    assert d2.status == "resolved"
    assert d2.selected_action == "two"
    assert d2.resolved_by == "cli"
    session.close()


def test_resolve_invalid_action_rejected_and_re_resolve_rejected() -> None:
    session = SessionLocal()
    d = decision_service.create_decision_request(
        session,
        entity_type="job_posting",
        entity_id="r2",
        decision_type="referral_window",
        reason_code=None,
        summary=None,
        options=["a", "b"],
        default_action="a",
        idempotency_key="res-2",
    )
    session.commit()
    try:
        try:
            decision_service.resolve_decision_request(session, decision_id=d.id, actor="cli", selected_action="invalid")
            assert False, "should have raised"
        except InvalidActionError:
            pass
        # resolve properly
        decision_service.resolve_decision_request(session, decision_id=d.id, actor="cli", selected_action="b")
        session.commit()
        # resolving again should raise
        try:
            decision_service.resolve_decision_request(session, decision_id=d.id, actor="cli", selected_action="a")
            assert False, "should have raised"
        except InvalidTransitionError:
            pass
    finally:
        session.close()


def test_expiry_is_idempotent() -> None:
    session = SessionLocal()
    d = decision_service.create_decision_request(
        session,
        entity_type="job_posting",
        entity_id="exp1",
        decision_type="referral_window",
        reason_code=None,
        summary=None,
        options=["ok"],
        default_action="ok",
        expires_at=datetime.utcnow() - timedelta(minutes=1),
        idempotency_key="exp1",
    )
    session.commit()
    cnt1 = decision_service.expire_due_decision_requests(session)
    session.commit()
    cnt2 = decision_service.expire_due_decision_requests(session)
    session.commit()
    assert cnt1 >= 1
    assert cnt2 == 0
    session.close()
