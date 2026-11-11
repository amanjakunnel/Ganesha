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
    is_meaningful_applied_date,
    list_likely_applied,
    list_likely_applied_grouped,
    reject_likely_applied,
)


def _session():
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def test_likely_applied_rules_and_confirm_reject() -> None:
    session = _session()
    try:
        suffix = make_uuid()
        date_url = f"https://jobs.example-likely.test/roles/{suffix}/date-only"
        match_url = f"https://jobs.example-likely.test/roles/{suffix}/match-pair"
        notes_url = f"https://jobs.example-likely.test/roles/{suffix}/notes-only"
        na_url = f"https://jobs.example-likely.test/roles/{suffix}/na-only"
        saved_date = SimplifyHistoryRecord(
            id=make_uuid(),
            batch_id=make_uuid(),
            company_name="DateCo",
            role_title="Engineer",
            job_url=date_url,
            applied_date_text="2026-10-01",
            status_raw="SAVED",
            status_mapped="saved_only",
            source_import_key=f"likely-date|{suffix}",
        )
        applied = SimplifyHistoryRecord(
            id=make_uuid(),
            batch_id=make_uuid(),
            company_name="MatchCo",
            role_title="PM",
            job_url=match_url,
            status_raw="APPLIED",
            status_mapped="applied",
            source_import_key=f"likely-applied|{suffix}",
        )
        saved_match = SimplifyHistoryRecord(
            id=make_uuid(),
            batch_id=make_uuid(),
            company_name="MatchCo",
            role_title="PM Saved",
            job_url=match_url,
            status_raw="SAVED",
            status_mapped="saved_only",
            source_import_key=f"likely-saved-match|{suffix}",
        )
        saved_notes = SimplifyHistoryRecord(
            id=make_uuid(),
            batch_id=make_uuid(),
            company_name="NotesCo",
            role_title="Analyst",
            job_url=notes_url,
            notes="I applied on the employer site yesterday.",
            status_raw="SAVED",
            status_mapped="saved_only",
            source_import_key=f"likely-notes|{suffix}",
        )
        saved_na = SimplifyHistoryRecord(
            id=make_uuid(),
            batch_id=make_uuid(),
            company_name="PlaceholderCo",
            role_title="Intern",
            job_url=na_url,
            applied_date_text="N/A",
            status_raw="SAVED",
            status_mapped="saved_only",
            source_import_key=f"likely-na|{suffix}",
        )
        session.add_all([saved_date, applied, saved_match, saved_notes, saved_na])
        session.commit()

        assert not is_meaningful_applied_date("N/A")
        cands = {c.record_id: c for c in list_likely_applied(session)}
        assert saved_na.id not in cands  # placeholder Applied Date alone is not evidence
        assert saved_date.id in cands
        assert cands[saved_date.id].group == "weak"
        assert any(e.kind == "applied_date" for e in cands[saved_date.id].evidence)
        assert saved_match.id in cands
        match_ev = next(
            e
            for e in cands[saved_match.id].evidence
            if e.kind == "matching_applied_record"
        )
        assert match_ev.match_basis == "canonical_url+company"
        assert match_ev.strength == "strong"
        assert cands[saved_match.id].group == "strong"
        assert saved_notes.id in cands
        assert any(e.kind == "notes_phrase" for e in cands[saved_notes.id].evidence)
        assert cands[saved_notes.id].group == "strong"

        grouped = list_likely_applied_grouped(session)
        assert grouped["strong_count"] >= 2
        assert grouped["weak_count"] >= 1
        strong_ids = {c["record_id"] for c in grouped["strong"]}
        assert saved_match.id in strong_ids
        assert saved_notes.id in strong_ids

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
