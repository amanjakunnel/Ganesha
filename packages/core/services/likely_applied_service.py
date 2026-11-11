"""Likely-applied review candidates from Simplify evidence (never auto-promote)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from packages.core.domain.models import AuditEvent, SimplifyHistoryRecord
from packages.core.services.importers.simplify_tracker_csv import (
    canonical_job_url,
    is_ambiguous_greenhouse_embed,
)

# Conservative notes phrases → evidence type "notes_phrase".
# Intentionally narrow; expand only with user-approved phrases.
NOTES_APPLIED_PHRASES: tuple[str, ...] = (
    r"\bapplied\b",
    r"\bsubmitted (my )?application\b",
    r"\bapplication submitted\b",
    r"\bi applied\b",
    r"\bsent application\b",
)

PROVENANCE_USER_CONFIRM = "user_confirmation_of_simplify_evidence"
REJECT_FLAG = "likely_applied_rejected"
CONFIRM_FLAG = "likely_applied_confirmed"


@dataclass
class LikelyAppliedEvidence:
    kind: str  # applied_date | matching_applied_record | notes_phrase | gmail_confirmation
    detail: str


@dataclass
class LikelyAppliedCandidate:
    record_id: str
    company: str
    role: str
    job_url: str | None
    status_mapped: str | None
    applied_date_text: str | None
    evidence: list[LikelyAppliedEvidence] = field(default_factory=list)
    low_confidence: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "company": self.company,
            "role": self.role,
            "job_url": self.job_url,
            "status_mapped": self.status_mapped,
            "applied_date_text": self.applied_date_text,
            "evidence": [asdict(e) for e in self.evidence],
            "low_confidence": self.low_confidence,
        }


def _notes_match(notes: str | None) -> str | None:
    if not notes:
        return None
    text = notes.strip()
    if not text:
        return None
    for pattern in NOTES_APPLIED_PHRASES:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return pattern
    return None


def _applied_url_index(session: Session) -> dict[str, SimplifyHistoryRecord]:
    index: dict[str, SimplifyHistoryRecord] = {}
    rows = (
        session.query(SimplifyHistoryRecord)
        .filter(SimplifyHistoryRecord.status_mapped == "applied")
        .all()
    )
    for row in rows:
        if is_ambiguous_greenhouse_embed(row.job_url or row.job_url_raw):
            continue
        url = canonical_job_url(row.job_url) or canonical_job_url(row.job_url_raw)
        if url:
            index[url] = row
    return index


def list_likely_applied(session: Session) -> list[LikelyAppliedCandidate]:
    """Saved-only rows with Simplify evidence they may actually be applied."""
    applied_urls = _applied_url_index(session)
    candidates: list[LikelyAppliedCandidate] = []
    rows = (
        session.query(SimplifyHistoryRecord)
        .filter(SimplifyHistoryRecord.status_mapped == "saved_only")
        .all()
    )
    for row in rows:
        if is_ambiguous_greenhouse_embed(row.job_url or row.job_url_raw):
            continue
        payload = dict(row.payload_json or {})
        if payload.get(REJECT_FLAG) or payload.get(CONFIRM_FLAG):
            continue
        evidence: list[LikelyAppliedEvidence] = []
        low = False

        if (row.applied_date_text or "").strip():
            evidence.append(
                LikelyAppliedEvidence(
                    kind="applied_date",
                    detail=f"Applied Date field = {row.applied_date_text.strip()}",
                )
            )

        url = canonical_job_url(row.job_url) or canonical_job_url(row.job_url_raw)
        if url and url in applied_urls:
            other = applied_urls[url]
            evidence.append(
                LikelyAppliedEvidence(
                    kind="matching_applied_record",
                    detail=(
                        f"Same canonical URL as APPLIED record "
                        f"{other.company_name} / {other.role_title}"
                    ),
                )
            )

        phrase = _notes_match(row.notes)
        if phrase:
            evidence.append(
                LikelyAppliedEvidence(
                    kind="notes_phrase",
                    detail=f"Notes matched /{phrase}/",
                )
            )

        gmail_hits = payload.get("gmail_match_evidence") or []
        if isinstance(gmail_hits, list):
            for hit in gmail_hits:
                if not isinstance(hit, dict):
                    continue
                evidence.append(
                    LikelyAppliedEvidence(
                        kind="gmail_confirmation",
                        detail=(
                            f"Gmail msg {hit.get('message_id')} "
                            f"from {hit.get('sender_domain')} "
                            f"({hit.get('confidence', 'matched')})"
                        ),
                    )
                )
                if hit.get("confidence") == "low":
                    low = True

        if not evidence:
            continue
        candidates.append(
            LikelyAppliedCandidate(
                record_id=row.id,
                company=row.company_name,
                role=row.role_title,
                job_url=row.job_url,
                status_mapped=row.status_mapped,
                applied_date_text=row.applied_date_text,
                evidence=evidence,
                low_confidence=low,
            )
        )
    return candidates


def confirm_likely_applied(session: Session, record_ids: list[str]) -> dict[str, Any]:
    confirmed = 0
    missing = 0
    for rid in record_ids:
        row = session.query(SimplifyHistoryRecord).filter_by(id=rid).one_or_none()
        if row is None:
            missing += 1
            continue
        payload = dict(row.payload_json or {})
        payload[CONFIRM_FLAG] = True
        payload[REJECT_FLAG] = False
        payload["status_provenance"] = PROVENANCE_USER_CONFIRM
        payload["confirmed_at"] = datetime.now(UTC).isoformat()
        row.payload_json = payload
        row.status_mapped = "applied"
        session.add(row)
        session.add(
            AuditEvent(
                entity_type="simplify_history",
                entity_id=row.id,
                event_type="likely_applied_confirmed",
                payload={"provenance": PROVENANCE_USER_CONFIRM},
            )
        )
        confirmed += 1
    session.flush()
    return {"confirmed": confirmed, "missing": missing}


def reject_likely_applied(session: Session, record_ids: list[str]) -> dict[str, Any]:
    rejected = 0
    missing = 0
    for rid in record_ids:
        row = session.query(SimplifyHistoryRecord).filter_by(id=rid).one_or_none()
        if row is None:
            missing += 1
            continue
        payload = dict(row.payload_json or {})
        payload[REJECT_FLAG] = True
        payload["rejected_at"] = datetime.now(UTC).isoformat()
        row.payload_json = payload
        # Stay saved_only; never re-suggest.
        session.add(row)
        session.add(
            AuditEvent(
                entity_type="simplify_history",
                entity_id=row.id,
                event_type="likely_applied_rejected",
                payload={},
            )
        )
        rejected += 1
    session.flush()
    return {"rejected": rejected, "missing": missing}


def notes_phrase_rules() -> list[str]:
    return list(NOTES_APPLIED_PHRASES)
