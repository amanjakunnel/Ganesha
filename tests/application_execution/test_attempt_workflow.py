from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from apps.application_execution.paths import EXECUTION_MODE_CHROME_SIMPLIFY
from apps.application_execution.services.artifact_selection import select_resume_artifact
from apps.application_execution.services.attempt_service import (
    AttemptBlockedError,
    DuplicateActiveAttemptError,
    attempt_public_dict,
    open_attempt_in_chrome,
    prepare_attempt,
    prepare_preview,
    transition_attempt,
)
from apps.application_execution.services.chrome_handoff import (
    SIMPLIFY_HANDOFF_INSTRUCTIONS,
    open_application_in_chrome,
)
from apps.application_execution.services.url_safety import is_safe_http_url
from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import (
    Base,
    Company,
    JobPosting,
    ResumeGeneration,
    make_dedupe_key,
    make_description_hash,
    make_uuid,
)
from packages.core.services.job_eligibility import ELIGIBILITY_EXCLUDED


@pytest.fixture()
def session() -> Session:
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _job(
    session: Session,
    *,
    title: str = "Software Engineer",
    company_name: str = "ApplyCo",
    url: str = "https://applyco.example/jobs/42",
    description: str | None = None,
    status: str = "new",
) -> JobPosting:
    company = Company(
        canonical_name=company_name,
        normalized_key=make_uuid()[:8],
    )
    session.add(company)
    session.flush()
    text = description or (
        "Software engineer building Python services on AWS with Docker and Kubernetes. "
        * 20
    )
    job = JobPosting(
        id=make_uuid(),
        source="csv_import",
        source_name="manual",
        source_type="csv_import",
        title=title,
        company_id=company.id,
        location="Austin, TX, United States",
        description_text=text,
        description_hash=make_description_hash(text),
        dedupe_key=make_dedupe_key(company_name, title, "Austin"),
        company_apply_url=url,
        canonical_url=url,
        status=status,
        scraped_at=datetime.now(UTC),
        discovered_at=datetime.now(UTC),
        source_import_key=f"apply|{make_uuid()}",
    )
    session.add(job)
    session.commit()
    return job


def _resume_gen(
    session: Session,
    job_id: str,
    tmp_path: Path,
    *,
    desktop_only: bool = False,
) -> ResumeGeneration:
    stamp = tmp_path / "generated" / job_id / "20260101T000000Z"
    stamp.mkdir(parents=True)
    docx = stamp / "Alex_Michael_Manjakunnel_Resume.docx"
    pdf = stamp / "Alex_Michael_Manjakunnel_Resume.pdf"
    docx.write_bytes(b"PK\x03\x04fake")
    pdf.write_bytes(b"%PDF-1.4 fake")
    desktop = tmp_path / "Desktop" / "Alex_Michael_Manjakunnel_Resume.pdf"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_bytes(b"%PDF-1.4 desktop")
    row = ResumeGeneration(
        id=make_uuid(),
        job_posting_id=job_id,
        status="succeeded",
        quality_status="active",
        selected_track="cloud",
        docx_path=None if desktop_only else str(docx),
        pdf_path=str(desktop) if desktop_only else str(pdf),
        desktop_pdf_path=str(desktop),
        validation_json={"ok": True, "page_count": 1},
    )
    session.add(row)
    session.commit()
    return row


def test_url_safety_rejects_localhost_and_non_http() -> None:
    assert is_safe_http_url("https://employer.example/apply")
    assert not is_safe_http_url("http://127.0.0.1/apply")
    assert not is_safe_http_url("file:///tmp/x")
    assert not is_safe_http_url("javascript:alert(1)")


def test_prepare_requires_job_specific_resume_not_desktop(
    session: Session, tmp_path: Path
) -> None:
    job = _job(session)
    _resume_gen(session, job.id, tmp_path, desktop_only=True)
    assert select_resume_artifact(session, job.id) is None
    preview = prepare_preview(session, job.id)
    assert preview.can_prepare is False or "validated_resume_for_this_job" in preview.missing
    with pytest.raises(AttemptBlockedError):
        prepare_attempt(
            session,
            job.id,
            acknowledge_fit_warning=True,
        )


def test_prepare_reuses_job_specific_resume(session: Session, tmp_path: Path) -> None:
    job = _job(session)
    gen = _resume_gen(session, job.id, tmp_path)
    preview = prepare_preview(session, job.id)
    assert preview.resume is not None
    assert preview.resume.generation_id == gen.id
    assert preview.cover_letter_optional is True
    assert preview.include_cover_letter_default is False
    assert preview.execution_mode == EXECUTION_MODE_CHROME_SIMPLIFY
    assert any("Simplify" in line or "simplify" in line.lower() for line in preview.simplify_limits)
    attempt = prepare_attempt(session, job.id, acknowledge_fit_warning=True)
    session.commit()
    assert attempt.status == "prepared"
    assert attempt.resume_generation_id == gen.id
    assert attempt.include_cover_letter is False
    assert attempt.package_json is not None
    assert attempt.package_json["desktop_pdf_forbidden"] is True
    assert attempt.package_json["ganesha_autofill"] is False
    assert attempt.package_json["api_key_required"] is False
    public = attempt_public_dict(attempt)
    assert public["simplify_integration"] is False
    assert public["autofill_by_ganesha"] is False


def test_optional_cover_letter_defaults_off(session: Session, tmp_path: Path) -> None:
    job = _job(session)
    _resume_gen(session, job.id, tmp_path)
    attempt = prepare_attempt(
        session, job.id, include_cover_letter=False, acknowledge_fit_warning=True
    )
    assert attempt.include_cover_letter is False
    assert attempt.cover_letter_stamp is None


def test_cover_letter_requested_without_artifact_blocks(
    session: Session, tmp_path: Path
) -> None:
    job = _job(session)
    _resume_gen(session, job.id, tmp_path)
    with pytest.raises(AttemptBlockedError, match="cover letter"):
        prepare_attempt(
            session,
            job.id,
            include_cover_letter=True,
            acknowledge_fit_warning=True,
        )


def test_excluded_eligibility_blocks_prepare(session: Session, tmp_path: Path) -> None:
    job = _job(
        session,
        description=(
            "Must hold an active TS/SCI clearance. US citizenship required. "
            "Software engineer building Python services. " * 15
        ),
    )
    _resume_gen(session, job.id, tmp_path)
    preview = prepare_preview(session, job.id)
    assert preview.eligibility == ELIGIBILITY_EXCLUDED
    assert preview.can_prepare is False
    with pytest.raises(AttemptBlockedError):
        prepare_attempt(session, job.id, acknowledge_fit_warning=True)


def test_missing_application_url_blocks(session: Session, tmp_path: Path) -> None:
    job = _job(session, url="")
    job.company_apply_url = None
    job.canonical_url = None
    session.commit()
    _resume_gen(session, job.id, tmp_path)
    preview = prepare_preview(session, job.id)
    assert preview.can_prepare is False
    assert "application_url" in preview.missing or preview.block_reason


def test_duplicate_active_attempt_requires_force(
    session: Session, tmp_path: Path
) -> None:
    job = _job(session)
    _resume_gen(session, job.id, tmp_path)
    first = prepare_attempt(session, job.id, acknowledge_fit_warning=True)
    session.commit()
    with pytest.raises(DuplicateActiveAttemptError):
        prepare_attempt(session, job.id, acknowledge_fit_warning=True)
    second = prepare_attempt(
        session, job.id, force_new=True, acknowledge_fit_warning=True
    )
    session.commit()
    assert second.id != first.id


def test_state_transitions_and_explicit_submission(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job(session)
    _resume_gen(session, job.id, tmp_path)
    attempt = prepare_attempt(session, job.id, acknowledge_fit_warning=True)
    session.commit()

    calls: list[str] = []

    def fake_open(*, intended_url: str, requested_url: str | None = None):
        from apps.application_execution.services.chrome_handoff import OpenResult

        calls.append(intended_url)
        return OpenResult(ok=True, detail="opened", url=intended_url)

    monkeypatch.setattr(
        "apps.application_execution.services.attempt_service.open_application_in_chrome",
        fake_open,
    )
    payload = open_attempt_in_chrome(session, attempt.id)
    session.commit()
    assert payload["submitted"] is False
    assert payload["autofill_by_ganesha"] is False
    assert payload["simplify_integration"] is False
    assert payload["simplify_handoff"] is True
    assert calls == [attempt.application_url]

    attempt = transition_attempt(session, attempt.id, "needs_input")
    session.commit()
    assert attempt.status == "needs_input"

    # Opening alone must never mark submitted.
    assert attempt.submitted_user_confirmed_at is None

    attempt = transition_attempt(session, attempt.id, "submitted_user_confirmed")
    session.commit()
    assert attempt.status == "submitted_user_confirmed"
    assert attempt.submitted_user_confirmed_at is not None


def test_cancel_attempt(session: Session, tmp_path: Path) -> None:
    job = _job(session)
    _resume_gen(session, job.id, tmp_path)
    attempt = prepare_attempt(session, job.id, acknowledge_fit_warning=True)
    session.commit()
    attempt = transition_attempt(session, attempt.id, "cancelled")
    session.commit()
    assert attempt.status == "cancelled"
    assert attempt.cancelled_at is not None


def test_open_refuses_url_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    result = open_application_in_chrome(
        intended_url="https://employer.example/apply",
        requested_url="https://evil.example/phish",
    )
    assert result.ok is False
    assert "differ" in result.detail.lower()


def test_sensitive_categories_listed_in_profile_ref(session: Session, tmp_path: Path) -> None:
    job = _job(session)
    _resume_gen(session, job.id, tmp_path)
    preview = prepare_preview(session, job.id)
    joined = " ".join(preview.sensitive_reminders).lower()
    assert "authorization" in joined
    assert "salary" in joined
    attempt = prepare_attempt(session, job.id, acknowledge_fit_warning=True)
    cats = attempt.profile_data_ref["fields_requiring_explicit_user_answer"]
    assert "work_authorization" in cats
    assert "sponsorship" in cats


def test_simplify_instructions_do_not_claim_integration() -> None:
    text = " ".join(SIMPLIFY_HANDOFF_INSTRUCTIONS).lower()
    assert "cannot" in text or "yourself" in text
    assert "ganesha cannot" in text or "cannot detect" in text
