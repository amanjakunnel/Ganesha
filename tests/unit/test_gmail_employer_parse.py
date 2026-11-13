"""Synthetic Gmail employer/role parsing (no live email / no PII fixtures)."""

from __future__ import annotations

from datetime import UTC, datetime

from packages.core.domain.models import SimplifyHistoryRecord, make_uuid
from packages.core.services.gmail_application_match import (
    DEFAULT_MAX_RESULTS,
    DEFAULT_QUERY,
    GmailMessageMeta,
    enrich_message_meta,
    match_meta_to_saved_only,
    parse_employer_and_role,
    parse_from_header,
    preview_gmail_matches,
)


def _saved(**kwargs: object) -> SimplifyHistoryRecord:
    defaults: dict[str, object] = dict(
        id=make_uuid(),
        batch_id=make_uuid(),
        company_name="Acme Technologies",
        role_title="Software Engineer New Grad",
        job_url="https://boards.greenhouse.io/acme/jobs/1",
        status_raw="SAVED",
        status_mapped="saved_only",
        source_import_key=f"gmail-emp|{make_uuid()}",
        created_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    defaults.update(kwargs)
    return SimplifyHistoryRecord(**defaults)


def test_parse_thank_you_for_applying_to() -> None:
    employer, role = parse_employer_and_role(
        subject="Thank you for applying to Acme Technologies",
        display_name="",
    )
    assert "acme" in employer.lower()


def test_parse_your_application_to() -> None:
    employer, _role = parse_employer_and_role(
        subject="Your application to Globex Corp",
    )
    assert "globex" in employer.lower()


def test_parse_application_received_role_at() -> None:
    employer, role = parse_employer_and_role(
        subject="Application received - Software Engineer at Initech",
    )
    assert "initech" in employer.lower()
    assert "software" in role.lower()


def test_display_name_fallback_for_ats_sender() -> None:
    display, domain = parse_from_header(
        '"Acme Careers" <noreply@greenhouse.io>'
    )
    assert domain == "greenhouse.io"
    assert "acme" in display.lower()
    employer, _ = parse_employer_and_role(
        subject="We have received your application",
        display_name=display,
    )
    assert "acme" in employer.lower()


def test_ats_domain_does_not_match_company_alone() -> None:
    row = _saved(company_name="Greenhouse")
    meta = GmailMessageMeta(
        message_id="m1",
        received_at=None,
        sender_domain="noreply.greenhouse.io",
        subject_hint="Thanks for applying",
        sender_display_name="",
    )
    enrich_message_meta(meta)
    matched, conf = match_meta_to_saved_only(meta, [row])
    # Without employer parse, ATS domain must not falsely match "Greenhouse" company.
    assert conf == "none" or matched is None or matched.company_name != "Greenhouse"


def test_greenhouse_subject_employer_matches_saved_only() -> None:
    row = _saved()
    meta = GmailMessageMeta(
        message_id="m2",
        received_at=datetime(2025, 7, 1, tzinfo=UTC),
        sender_domain="noreply.greenhouse.io",
        subject_hint="Thank you for applying to Acme Technologies — Software Engineer New Grad",
        sender_display_name="Greenhouse",
    )
    enrich_message_meta(meta)
    assert "acme" in meta.parsed_employer.lower()
    matched, conf = match_meta_to_saved_only(meta, [row])
    assert matched is not None
    assert conf in {"matched", "low"}
    assert matched.company_name == "Acme Technologies"


def test_workday_amazon_ashby_lever_query_covers_platforms() -> None:
    assert "greenhouse" in DEFAULT_QUERY
    assert "lever.co" in DEFAULT_QUERY
    assert "ashby" in DEFAULT_QUERY
    assert "workday" in DEFAULT_QUERY
    assert "amazon" in DEFAULT_QUERY
    assert DEFAULT_MAX_RESULTS >= 100


def test_preview_persists_only_allowed_fields() -> None:
    from packages.core.db import SessionLocal, get_engine
    from packages.core.domain.models import Base

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        session.add(_saved())
        session.commit()
        meta = GmailMessageMeta(
            message_id="m-prev",
            received_at=None,
            sender_domain="noreply.greenhouse.io",
            subject_hint="Thank you for applying to Acme Technologies — Software Engineer New Grad",
            sender_display_name="Acme Careers",
        )
        enrich_message_meta(meta)
        preview = preview_gmail_matches(session, messages=[meta], persist=False)
        assert preview.dry_run is True
        assert preview.scanned == 1
        if preview.matches:
            keys = set(preview.matches[0].keys())
            assert "subject" not in keys
            assert "subject_hint" not in keys
            assert "body" not in keys
            assert "sender_display_name" not in keys
            assert {"message_id", "received_at", "sender_domain", "matched_company", "matched_role"} <= keys
    finally:
        session.close()
