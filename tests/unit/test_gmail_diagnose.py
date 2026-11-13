"""Synthetic Gmail diagnose / match tests (no live email / no PII fixtures)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import Base, SimplifyHistoryRecord, make_uuid
from packages.core.services.gmail_application_match import (
    DEFAULT_QUERY,
    GmailMessageMeta,
    build_search_params,
    diagnose_gmail_preview,
    match_meta_to_saved_only,
    parse_employer_and_role,
    parse_from_header,
    preview_gmail_matches,
)


def _session() -> Session:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def _saved_row(**kwargs: Any) -> SimplifyHistoryRecord:
    defaults: dict[str, Any] = dict(
        id=make_uuid(),
        batch_id=make_uuid(),
        company_name="Acme Technologies",
        role_title="Software Engineer New Grad",
        job_url="https://example.com/jobs/acme",
        status_raw="SAVED",
        status_mapped="saved_only",
        source_import_key=f"gmail-diag|{make_uuid()}",
        created_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    defaults.update(kwargs)
    return SimplifyHistoryRecord(**defaults)


def test_build_search_params_exposes_paging_and_query() -> None:
    params = build_search_params(days=400)
    assert "after:" in params.query
    assert "thank you for applying" in DEFAULT_QUERY
    assert "application was received" in DEFAULT_QUERY
    assert "your application to" in DEFAULT_QUERY
    assert "from:noreply@greenhouse.io" in DEFAULT_QUERY
    assert "from:myworkday.com" in DEFAULT_QUERY
    assert "from:amazon.jobs" in DEFAULT_QUERY
    assert params.max_results == 200
    assert params.page_token_used is True
    assert params.days == 400


def test_diagnose_aggregates_domains_only() -> None:
    session = _session()
    try:
        row = _saved_row()
        session.add(row)
        session.commit()
        messages = [
            GmailMessageMeta(
                message_id="m1",
                received_at=datetime(2025, 7, 1, tzinfo=UTC),
                sender_domain="careers.acme.com",
                subject_hint="Thank you for applying — Software Engineer New Grad",
            ),
            GmailMessageMeta(
                message_id="m2",
                received_at=datetime(2025, 7, 2, tzinfo=UTC),
                sender_domain="noreply.greenhouse.io",
                subject_hint="Your application was received",
            ),
            GmailMessageMeta(
                message_id="m3",
                received_at=None,
                sender_domain="newsletter.example.com",
                subject_hint="Weekly digest",
            ),
        ]
        diag = diagnose_gmail_preview(session, messages=messages, days=400)
        payload = diag.to_dict()
        assert payload["scanned"] == 3
        assert payload["matched"] >= 1
        assert "careers.acme.com" in payload["sender_domain_histogram"]
        assert payload["confirmation_looking"] >= 2
        # Never leak subjects into diagnose output.
        blob = json_dumps_safe(payload)
        assert "Thank you for applying" not in blob
        assert "Weekly digest" not in blob
        assert "subject_hint" not in blob
    finally:
        session.close()


def json_dumps_safe(obj: object) -> str:
    import json

    return json.dumps(obj)


def test_company_plus_generic_engineer_is_low_confidence() -> None:
    row = _saved_row(role_title="Backend Platform Engineer")
    meta = GmailMessageMeta(
        message_id="m-low",
        received_at=None,
        sender_domain="jobs.acme.com",
        subject_hint="Thanks for applying — Software Engineer",
        sender_display_name="Acme Technologies",
        parsed_employer="Acme Technologies",
    )
    matched, conf = match_meta_to_saved_only(meta, [row])
    assert matched is not None
    assert conf == "low"


def test_parse_employer_from_display_and_subject() -> None:
    display, domain = parse_from_header(
        '"Stripe Careers" <noreply@greenhouse.io>'
    )
    assert domain == "greenhouse.io"
    assert "Stripe" in display
    employer, role = parse_employer_and_role(
        subject="Thank you for applying to Stripe",
        display_name=display,
    )
    assert "stripe" in employer.lower()

    employer2, role2 = parse_employer_and_role(
        subject="Application received - Backend Engineer at Databricks",
        display_name="",
    )
    assert "databricks" in employer2.lower()
    assert "backend" in role2.lower()

    employer3, _role3 = parse_employer_and_role(
        subject="Your application to Notion",
        display_name="",
    )
    assert "notion" in employer3.lower()


def test_ats_domain_does_not_count_as_employer() -> None:
    """Greenhouse/Lever domains must not match company aliases via domain alone."""
    row = _saved_row(company_name="Greenhouse", role_title="Software Engineer")
    meta = GmailMessageMeta(
        message_id="m-ats",
        received_at=None,
        sender_domain="noreply.greenhouse.io",
        subject_hint="Thanks for applying",
        sender_display_name="",
        parsed_employer="",
        parsed_role="",
    )
    matched, conf = match_meta_to_saved_only(meta, [row])
    assert conf == "none"
    assert matched is None

    # Display-name / subject employer wins even when domain is ATS.
    meta2 = GmailMessageMeta(
        message_id="m-ats2",
        received_at=None,
        sender_domain="noreply.greenhouse.io",
        subject_hint="Thank you for applying to Acme Technologies — Software Engineer New Grad",
        sender_display_name="Acme Technologies",
        parsed_employer="Acme Technologies",
        parsed_role="Software Engineer New Grad",
    )
    matched2, conf2 = match_meta_to_saved_only(meta2, [row := _saved_row()])
    assert matched2 is not None
    assert conf2 == "matched"


def test_preview_default_persist_false_and_search_fields() -> None:
    session = _session()
    try:
        session.add(_saved_row())
        session.commit()
        meta = GmailMessageMeta(
            message_id="m-prev",
            received_at=None,
            sender_domain="careers.acme.com",
            subject_hint="We have received your application — Software Engineer New Grad",
        )
        preview = preview_gmail_matches(session, messages=[meta], persist=False)
        assert preview.dry_run is True
        assert preview.search is not None
        assert preview.search.get("page_token_paging") is True
        assert "max_results" in preview.search
        assert "subject" not in (preview.matches[0] if preview.matches else {})
    finally:
        session.close()
