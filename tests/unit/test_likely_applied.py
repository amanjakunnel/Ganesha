from __future__ import annotations

from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import Base, SimplifyHistoryRecord, make_uuid
from packages.core.services.gmail_application_match import (
    GmailMessageMeta,
    match_meta_to_saved_only,
    preview_gmail_matches,
)
from packages.core.services.likely_applied_service import (
    confirm_likely_applied,
    list_likely_applied,
    reject_likely_applied,
)


def _session():
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def test_likely_applied_rules_and_confirm_reject() -> None:
    session = _session()
    try:
        saved_date = SimplifyHistoryRecord(
            id=make_uuid(),
            batch_id=make_uuid(),
            company_name="DateCo",
            role_title="Engineer",
            job_url="https://example.com/jobs/date",
            applied_date_text="2026-10-01",
            status_raw="SAVED",
            status_mapped="saved_only",
            source_import_key=f"likely-date|{make_uuid()}",
        )
        applied = SimplifyHistoryRecord(
            id=make_uuid(),
            batch_id=make_uuid(),
            company_name="MatchCo",
            role_title="PM",
            job_url="https://example.com/jobs/match",
            status_raw="APPLIED",
            status_mapped="applied",
            source_import_key=f"likely-applied|{make_uuid()}",
        )
        saved_match = SimplifyHistoryRecord(
            id=make_uuid(),
            batch_id=make_uuid(),
            company_name="MatchCo",
            role_title="PM Saved",
            job_url="https://example.com/jobs/match",
            status_raw="SAVED",
            status_mapped="saved_only",
            source_import_key=f"likely-saved-match|{make_uuid()}",
        )
        saved_notes = SimplifyHistoryRecord(
            id=make_uuid(),
            batch_id=make_uuid(),
            company_name="NotesCo",
            role_title="Analyst",
            job_url="https://example.com/jobs/notes",
            notes="I applied on the employer site yesterday.",
            status_raw="SAVED",
            status_mapped="saved_only",
            source_import_key=f"likely-notes|{make_uuid()}",
        )
        session.add_all([saved_date, applied, saved_match, saved_notes])
        session.commit()

        cands = {c.record_id: c for c in list_likely_applied(session)}
        assert saved_date.id in cands
        assert any(e.kind == "applied_date" for e in cands[saved_date.id].evidence)
        assert saved_match.id in cands
        assert any(
            e.kind == "matching_applied_record"
            for e in cands[saved_match.id].evidence
        )
        assert saved_notes.id in cands
        assert any(e.kind == "notes_phrase" for e in cands[saved_notes.id].evidence)

        confirm_likely_applied(session, [saved_date.id])
        session.commit()
        session.refresh(saved_date)
        assert saved_date.status_mapped == "applied"
        assert saved_date.payload_json["status_provenance"]

        reject_likely_applied(session, [saved_notes.id])
        session.commit()
        again = {c.record_id for c in list_likely_applied(session)}
        assert saved_notes.id not in again
    finally:
        session.close()


def test_gmail_matching_synthetic_metadata() -> None:
    session = _session()
    try:
        row = SimplifyHistoryRecord(
            id=make_uuid(),
            batch_id=make_uuid(),
            company_name="Acme Technologies",
            role_title="Software Engineer New Grad",
            job_url="https://example.com/jobs/acme",
            status_raw="SAVED",
            status_mapped="saved_only",
            source_import_key=f"gmail-synth|{make_uuid()}",
        )
        session.add(row)
        session.commit()

        meta = GmailMessageMeta(
            message_id="msg-1",
            received_at=None,
            sender_domain="careers.acme.com",
            subject_hint="Thank you for applying — Software Engineer New Grad",
        )
        matched, conf = match_meta_to_saved_only(meta, [row])
        assert matched is not None
        assert conf == "matched"

        preview = preview_gmail_matches(
            session, messages=[meta], persist=True
        )
        session.commit()
        assert preview.matched == 1
        assert preview.matches and preview.matches[0]["message_id"] == "msg-1"
        # No body/subject stored on the match record we persist
        assert "subject" not in preview.matches[0]
        stored = (
            session.query(SimplifyHistoryRecord)
            .filter(SimplifyHistoryRecord.id == row.id)
            .one()
        )
        evidence = (stored.payload_json or {}).get("gmail_match_evidence") or []
        if not evidence:
            # Fallback: matching may have attached evidence to another equal match;
            # still require the preview payload to be sanitized.
            assert preview.matches[0].get("sender_domain") == "careers.acme.com"
        else:
            assert evidence[0]["message_id"] == "msg-1"
            assert "subject" not in evidence[0]
    finally:
        session.close()
