from __future__ import annotations

from datetime import datetime
from typing import Iterable, List

from sqlalchemy.orm import Session

from packages.core.domain.models import AuditEvent, DecisionRequest


class DecisionError(Exception):
    pass


class DecisionNotFoundError(DecisionError):
    pass


class InvalidTransitionError(DecisionError):
    pass


class InvalidActionError(DecisionError):
    pass


# Allowed decision types for now
DECISION_TYPES = {
    "referral_window",
    "track_selection",
    "evidence_gap",
    "sensitive_question",
    "duplicate_candidate",
    "protected_company",
    "system_failure",
}


def create_decision_request(
    session: Session,
    *,
    entity_type: str,
    entity_id: str | None,
    decision_type: str,
    reason_code: str | None,
    summary: str | None,
    options: Iterable[str],
    default_action: str,
    expires_at: datetime | None = None,
    idempotency_key: str | None = None,
) -> DecisionRequest:
    """Create a pending decision request.

    - idempotency: if idempotency_key provided and exists, return existing.
    - options must be non-empty and default_action must be in options.
    - expires_at, if provided, must be in the future (UTC naive datetime accepted).
    """
    opts = list(options or [])
    if not opts:
        raise DecisionError("options must be a non-empty iterable")
    if default_action not in opts:
        raise DecisionError("default_action must be one of the provided options")
    if expires_at is not None and expires_at <= datetime.utcnow():
        raise DecisionError("expires_at must be in the future")

    if idempotency_key:
        existing = (
            session.query(DecisionRequest)
            .filter(DecisionRequest.idempotency_key == idempotency_key)
            .one_or_none()
        )
        if existing:
            return existing

    dr = DecisionRequest(
        entity_type=entity_type,
        entity_id=entity_id,
        decision_type=decision_type,
        status="pending",
        reason_code=reason_code,
        summary=summary,
        options_json=opts,
        default_action=default_action,
        expires_at=expires_at,
        idempotency_key=idempotency_key,
    )
    session.add(dr)
    ev = AuditEvent(entity_type="decision_request", entity_id=dr.id, event_type="decision_request_created", payload={
        "decision_type": decision_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "idempotency_key": idempotency_key,
    })
    session.add(ev)
    session.flush()
    return dr


def list_pending_decisions(
    session: Session,
    *,
    decision_type: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> List[DecisionRequest]:
    """Return pending, non-expired decision requests.

    Ordered by earliest expires_at then created_at for determinism.
    """
    now = datetime.utcnow()
    q = session.query(DecisionRequest).filter(DecisionRequest.status == "pending")
    # exclude expired
    q = q.filter((DecisionRequest.expires_at.is_(None)) | (DecisionRequest.expires_at > now))
    if decision_type:
        q = q.filter(DecisionRequest.decision_type == decision_type)
    if entity_type:
        q = q.filter(DecisionRequest.entity_type == entity_type)
    if entity_id:
        q = q.filter(DecisionRequest.entity_id == entity_id)
    q = q.order_by(DecisionRequest.expires_at.asc().nulls_last(), DecisionRequest.created_at.asc())
    return q.all()


def get_decision_request(session: Session, decision_id: str) -> DecisionRequest:
    d = session.query(DecisionRequest).filter(DecisionRequest.id == decision_id).one_or_none()
    if not d:
        raise DecisionNotFoundError(f"DecisionRequest not found: {decision_id}")
    return d


def resolve_decision_request(
    session: Session,
    *,
    decision_id: str,
    actor: str,
    selected_action: str,
    note: str | None = None,
) -> DecisionRequest:
    d = session.query(DecisionRequest).filter(DecisionRequest.id == decision_id).one_or_none()
    if not d:
        raise DecisionNotFoundError(decision_id)
    if d.status != "pending":
        raise InvalidTransitionError(f"Decision is not pending (status={d.status})")
    opts = list(d.options_json or [])
    if selected_action not in opts:
        raise InvalidActionError("selected_action is not one of the options")
    d.status = "resolved"
    d.resolved_at = datetime.utcnow()
    d.resolved_by = actor
    d.selected_action = selected_action
    d.resolution_note = note
    ev = AuditEvent(entity_type="decision_request", entity_id=d.id, event_type="decision_request_resolved", payload={
        "actor": actor,
        "selected_action": selected_action,
        "note": note,
    })
    session.add(ev)
    session.add(d)
    session.flush()
    return d


def expire_due_decision_requests(session: Session, *, now: datetime | None = None) -> int:
    """Expire pending decisions whose expires_at <= now.

    Sets status to expired and records the default_action as selected_action.
    Returns number expired.
    """
    if now is None:
        now = datetime.utcnow()
    pending = (
        session.query(DecisionRequest)
        .filter(DecisionRequest.status == "pending")
        .filter(DecisionRequest.expires_at.is_not(None))
        .filter(DecisionRequest.expires_at <= now)
        .all()
    )
    count = 0
    for d in pending:
        d.status = "expired"
        d.resolved_at = now
        d.selected_action = d.default_action
        d.resolution_note = "expired"
        ev = AuditEvent(entity_type="decision_request", entity_id=d.id, event_type="decision_request_expired", payload={
            "default_action": d.default_action,
        })
        session.add(ev)
        session.add(d)
        count += 1
    if count:
        session.flush()
    return count


def supersede_decision_request(session: Session, *, decision_id: str, by_id: str | None = None, actor: str | None = None) -> DecisionRequest:
    d = session.query(DecisionRequest).filter(DecisionRequest.id == decision_id).one_or_none()
    if not d:
        raise DecisionNotFoundError(decision_id)
    if d.status != "pending":
        raise InvalidTransitionError("only pending decisions can be superseded")
    d.status = "superseded"
    d.resolved_at = datetime.utcnow()
    d.resolved_by = actor
    d.selected_action = None
    d.resolution_note = f"superseded by {by_id}" if by_id else "superseded"
    ev = AuditEvent(entity_type="decision_request", entity_id=d.id, event_type="decision_request_superseded", payload={
        "by_id": by_id,
        "actor": actor,
    })
    session.add(ev)
    session.add(d)
    session.flush()
    return d
