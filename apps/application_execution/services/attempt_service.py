from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, joinedload

from apps.application_execution.paths import (
    ACTIVE_ATTEMPT_STATUSES,
    ATTEMPT_STATUSES,
    EXECUTION_MODE_CHROME_SIMPLIFY,
    attempt_dir,
)
from apps.application_execution.schemas import PreparePreview
from apps.application_execution.services.artifact_selection import (
    application_destination,
    select_cover_letter_artifact,
    select_resume_artifact,
)
from apps.application_execution.services.chrome_handoff import (
    SIMPLIFY_HANDOFF_INSTRUCTIONS,
    open_application_in_chrome,
    reveal_local_path,
)
from apps.application_execution.services.profile_ref import (
    build_profile_data_ref,
    sensitive_question_reminders,
)
from apps.application_execution.services.url_safety import is_safe_http_url
from apps.dashboard.services.jobs_dashboard_service import company_name_for
from apps.resume_generation.services.generation_preflight import generation_preflight
from packages.core.domain.models import (
    ApplicationAttempt,
    AuditEvent,
    JobApplication,
    JobPosting,
    make_uuid,
)
from packages.core.services.application_service import (
    create_application_draft,
    transition_application,
)
from packages.core.services.evidence_fit_service import (
    BAND_LOW_FIT,
    BAND_STRETCH,
    get_stored_fit,
)
from packages.core.services.job_eligibility import (
    ELIGIBILITY_EXCLUDED,
    ELIGIBILITY_NEEDS_REVIEW,
    resolve_job_eligibility,
)
from packages.core.services.job_service import JobNotFoundError


class AttemptError(Exception):
    pass


class AttemptNotFoundError(AttemptError):
    pass


class AttemptBlockedError(AttemptError):
    pass


class DuplicateActiveAttemptError(AttemptError):
    def __init__(self, message: str, active: list[ApplicationAttempt]) -> None:
        super().__init__(message)
        self.active = active


_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "prepared": {
        "opened",
        "needs_input",
        "ready_for_review",
        "submitted_user_confirmed",
        "cancelled",
        "failed",
    },
    "opened": {
        "needs_input",
        "ready_for_review",
        "submitted_user_confirmed",
        "cancelled",
        "failed",
    },
    "needs_input": {
        "opened",
        "ready_for_review",
        "submitted_user_confirmed",
        "cancelled",
        "failed",
    },
    "ready_for_review": {
        "submitted_user_confirmed",
        "needs_input",
        "cancelled",
        "failed",
    },
    "submitted_user_confirmed": set(),
    "failed": set(),
    "cancelled": set(),
}


def _attempt_summary(row: ApplicationAttempt) -> dict[str, Any]:
    return {
        "attempt_id": row.id,
        "status": row.status,
        "execution_mode": row.execution_mode,
        "prepared_at": row.prepared_at.isoformat() if row.prepared_at else None,
        "opened_at": row.opened_at.isoformat() if row.opened_at else None,
        "include_cover_letter": row.include_cover_letter,
    }


def list_attempts_for_job(session: Session, job_id: str) -> list[ApplicationAttempt]:
    return (
        session.query(ApplicationAttempt)
        .filter(ApplicationAttempt.job_posting_id == job_id)
        .order_by(ApplicationAttempt.created_at.desc())
        .all()
    )


def get_attempt(session: Session, attempt_id: str) -> ApplicationAttempt:
    row = (
        session.query(ApplicationAttempt)
        .filter(ApplicationAttempt.id == attempt_id)
        .one_or_none()
    )
    if row is None:
        raise AttemptNotFoundError(attempt_id)
    return row


def active_attempts(session: Session, job_id: str) -> list[ApplicationAttempt]:
    return [
        row
        for row in list_attempts_for_job(session, job_id)
        if row.status in ACTIVE_ATTEMPT_STATUSES
    ]


def prepare_preview(session: Session, job_id: str) -> PreparePreview:
    job = (
        session.query(JobPosting)
        .options(joinedload(JobPosting.company))
        .filter(JobPosting.id == job_id)
        .one_or_none()
    )
    if job is None:
        raise JobNotFoundError(job_id)

    eligibility = resolve_job_eligibility(job)
    application_url, posting_url = application_destination(job)
    resume = select_resume_artifact(session, job_id)
    cover = select_cover_letter_artifact(job_id)
    fit = get_stored_fit(job) or {}
    fit_band = fit.get("band")
    fit_warning = None
    if fit_band in {BAND_STRETCH, BAND_LOW_FIT}:
        fit_warning = fit.get("short_reason") or (
            "Stretch/low-fit job: review gaps before applying."
        )

    missing: list[str] = []
    block_reason: str | None = None
    can_prepare = True

    if eligibility.result == ELIGIBILITY_EXCLUDED:
        can_prepare = False
        block_reason = (
            "Application preparation is unavailable because this job does not meet "
            "your configured eligibility policy."
        )
    else:
        gen_block = generation_preflight(session, job)
        if gen_block and resume is None:
            # Needs-review / missing docs: still allow prepare if URL+identity ok,
            # but surface generation block as missing info.
            missing.append(gen_block)

    if not application_url or not is_safe_http_url(application_url):
        can_prepare = False
        block_reason = block_reason or "Missing or invalid application URL."
        missing.append("application_url")

    company = company_name_for(job)
    role = (job.title or "").strip()
    if not role or company.strip().lower() == "unknown company":
        can_prepare = False
        block_reason = block_reason or "Company or role identity is incomplete."
        missing.append("identity")

    if resume is None:
        missing.append("validated_resume_for_this_job")
        can_prepare = False
        block_reason = block_reason or (
            "Generate a validated job-specific resume before preparing an application."
        )

    if eligibility.result == ELIGIBILITY_NEEDS_REVIEW and can_prepare:
        missing.append("eligibility_needs_review")

    profile = build_profile_data_ref(session)
    raw_fields = profile.get("fields_available")
    fields = [str(item) for item in raw_fields] if isinstance(raw_fields, list) else []
    if "name" not in fields or "email" not in fields:
        missing.append("basic_profile_contact")

    active = [_attempt_summary(a) for a in active_attempts(session, job_id)]

    previously_applied = None
    try:
        from packages.core.services.importers.simplify_tracker_csv import (
            previously_applied_for_job,
        )

        previously_applied = previously_applied_for_job(session, job)
    except Exception:
        previously_applied = None

    # Also warn on likely-applied review candidates (never auto-block silently).
    if previously_applied is None:
        try:
            from packages.core.services.likely_applied_service import list_likely_applied
            from packages.core.services.importers.simplify_tracker_csv import (
                canonical_job_url,
            )

            urls = {
                u
                for u in (
                    canonical_job_url(job.company_apply_url),
                    canonical_job_url(job.canonical_url),
                )
                if u
            }
            for cand in list_likely_applied(session):
                if cand.job_url and cand.job_url in urls:
                    previously_applied = {
                        "source": "likely_applied_review",
                        "status": "likely_applied",
                        "company": cand.company,
                        "role": cand.role,
                        "requires_explicit_continue": True,
                        "message": (
                            "This posting matches a 'likely applied' review candidate. "
                            "Confirm or reject it under /likely-applied, or explicitly continue."
                        ),
                    }
                    break
        except Exception:
            pass

    liveness_payload = None
    try:
        from packages.core.services.job_liveness import check_job_liveness

        live = check_job_liveness(session, job, mark_closed=True)
        liveness_payload = live.to_dict()
        if not live.live:
            can_prepare = False
            block_reason = block_reason or (
                "Posting appears closed or unreachable (liveness check)."
            )
            missing.append("listing_not_live")
    except Exception:
        liveness_payload = {"live": None, "reason": "liveness_check_skipped"}

    return PreparePreview(
        job_id=job_id,
        company=company,
        role=role or "Untitled",
        posting_url=posting_url,
        application_url=application_url,
        can_prepare=can_prepare and block_reason is None,
        block_reason=block_reason,
        fit_band=fit_band if isinstance(fit_band, str) else None,
        fit_warning=fit_warning if isinstance(fit_warning, str) else None,
        eligibility=eligibility.result,
        execution_mode=EXECUTION_MODE_CHROME_SIMPLIFY,
        resume=resume,
        cover_letter=cover,
        cover_letter_optional=True,
        include_cover_letter_default=False,
        missing=missing,
        sensitive_reminders=sensitive_question_reminders(),
        active_attempts=active,
        simplify_limits=list(SIMPLIFY_HANDOFF_INSTRUCTIONS),
        profile_fields_available=fields,
        previously_applied=previously_applied,
        liveness=liveness_payload,
    )


def _write_package_sidecar(attempt: ApplicationAttempt) -> Path:
    directory = attempt_dir(attempt.job_posting_id, attempt.id)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "attempt_id": attempt.id,
        "job_id": attempt.job_posting_id,
        "company": attempt.company_name,
        "role": attempt.role_title,
        "posting_url": attempt.posting_url,
        "application_url": attempt.application_url,
        "resume_generation_id": attempt.resume_generation_id,
        "cover_letter_stamp": attempt.cover_letter_stamp,
        "include_cover_letter": attempt.include_cover_letter,
        "execution_mode": attempt.execution_mode,
        "status": attempt.status,
        "profile_data_ref": attempt.profile_data_ref,
        "package": attempt.package_json,
        "unresolved": attempt.unresolved_json,
        "prepared_at": attempt.prepared_at.isoformat() if attempt.prepared_at else None,
    }
    path = directory / "package.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def prepare_attempt(
    session: Session,
    job_id: str,
    *,
    include_cover_letter: bool = False,
    force_new: bool = False,
    acknowledge_fit_warning: bool = False,
    acknowledge_previous_application: bool = False,
) -> ApplicationAttempt:
    preview = prepare_preview(session, job_id)
    if not preview.can_prepare:
        raise AttemptBlockedError(preview.block_reason or "Cannot prepare application.")

    if preview.fit_warning and not acknowledge_fit_warning:
        raise AttemptBlockedError(
            "This job has a fit warning. Acknowledge the stretch/low-fit warning to continue."
        )

    if preview.previously_applied and not acknowledge_previous_application:
        raise AttemptBlockedError(
            (preview.previously_applied.get("message") if preview.previously_applied else None)
            or "Previously applied — acknowledge to continue with another attempt."
        )

    active = active_attempts(session, job_id)
    if active and not force_new:
        raise DuplicateActiveAttemptError(
            "An active application attempt already exists for this job. "
            "Cancel it or pass force_new to start another.",
            active=active,
        )

    if preview.resume is None:
        raise AttemptBlockedError(
            "No validated job-specific resume is available. Generate one first, "
            "then prepare the application."
        )

    if include_cover_letter and preview.cover_letter is None:
        raise AttemptBlockedError(
            "Cover letter was requested but no validated job-specific cover letter exists."
        )

    job_app = create_application_draft(session, job_id)
    if job_app.status == "draft":
        try:
            transition_application(session, job_app.id, "ready_to_apply")
        except Exception:
            # Keep draft if transition is unavailable; attempt still records prep.
            pass

    now = datetime.now(UTC)
    profile = build_profile_data_ref(session)
    raw_sensitive = profile.get("fields_requiring_explicit_user_answer")
    sensitive_cats = (
        {str(item) for item in raw_sensitive} if isinstance(raw_sensitive, list) else set()
    )
    unresolved: list[dict[str, str]] = [
        {
            "kind": "sensitive_question_category",
            "category": category,
            "action": "answer_manually_on_employer_form",
        }
        for category in sorted(sensitive_cats)
    ]
    unresolved.append(
        {
            "kind": "final_submission",
            "action": "user_reviews_and_submits_manually",
        }
    )
    if preview.missing:
        for item in preview.missing:
            if item in {"validated_resume_for_this_job"}:
                continue
            unresolved.append({"kind": "missing_info", "item": item})

    package = {
        "resume": preview.resume.to_dict() if preview.resume else None,
        "cover_letter": (
            preview.cover_letter.to_dict()
            if include_cover_letter and preview.cover_letter
            else None
        ),
        "desktop_pdf_forbidden": True,
        "simplify_mode": "user_driven_handoff",
        "ganesha_autofill": False,
        "api_key_required": False,
        "fit_band": preview.fit_band,
        "fit_warning_acknowledged": bool(preview.fit_warning),
        "instructions": list(SIMPLIFY_HANDOFF_INSTRUCTIONS),
    }

    attempt = ApplicationAttempt(
        id=make_uuid(),
        job_posting_id=job_id,
        job_application_id=job_app.id,
        company_name=preview.company,
        role_title=preview.role,
        posting_url=preview.posting_url,
        application_url=preview.application_url or "",
        resume_generation_id=preview.resume.generation_id if preview.resume else None,
        cover_letter_stamp=(
            preview.cover_letter.stamp
            if include_cover_letter and preview.cover_letter
            else None
        ),
        cover_letter_optional=True,
        include_cover_letter=bool(include_cover_letter and preview.cover_letter),
        execution_mode=EXECUTION_MODE_CHROME_SIMPLIFY,
        status="prepared",
        profile_data_ref=profile,
        package_json=package,
        unresolved_json=unresolved,
        prepared_at=now,
    )
    session.add(attempt)
    session.add(
        AuditEvent(
            entity_type="application_attempt",
            entity_id=attempt.id,
            event_type="application_attempt_prepared",
            payload={
                "job_posting_id": job_id,
                "execution_mode": attempt.execution_mode,
                "include_cover_letter": attempt.include_cover_letter,
            },
        )
    )
    session.flush()
    _write_package_sidecar(attempt)
    return attempt


def transition_attempt(
    session: Session,
    attempt_id: str,
    new_status: str,
    *,
    note: str | None = None,
) -> ApplicationAttempt:
    if new_status not in ATTEMPT_STATUSES:
        raise AttemptError(f"Unknown attempt status: {new_status}")
    attempt = get_attempt(session, attempt_id)
    allowed = _ALLOWED_TRANSITIONS.get(attempt.status, set())
    if new_status not in allowed:
        raise AttemptError(f"Cannot move attempt {attempt.status} -> {new_status}")
    previous = attempt.status
    attempt.status = new_status
    now = datetime.now(UTC)
    if new_status == "opened":
        attempt.opened_at = now
    elif new_status == "submitted_user_confirmed":
        attempt.submitted_user_confirmed_at = now
        if attempt.job_application_id:
            app = (
                session.query(JobApplication)
                .filter(JobApplication.id == attempt.job_application_id)
                .one_or_none()
            )
            if app and app.status in {"draft", "ready_to_apply"}:
                transition_application(
                    session,
                    app.id,
                    "submitted",
                    note=note or "User confirmed submission in Ganesha",
                )
    elif new_status == "cancelled":
        attempt.cancelled_at = now
    elif new_status == "failed" and note:
        attempt.safe_error = note
    session.add(
        AuditEvent(
            entity_type="application_attempt",
            entity_id=attempt.id,
            event_type="application_attempt_status_changed",
            payload={"from": previous, "to": new_status, "note": note},
        )
    )
    session.flush()
    _write_package_sidecar(attempt)
    return attempt


def open_attempt_in_chrome(session: Session, attempt_id: str) -> dict[str, Any]:
    attempt = get_attempt(session, attempt_id)
    if attempt.status in {"cancelled", "submitted_user_confirmed", "failed"}:
        raise AttemptError(f"Cannot open attempt in status {attempt.status}")
    result = open_application_in_chrome(intended_url=attempt.application_url)
    if not result.ok:
        attempt.safe_error = result.detail
        session.flush()
        raise AttemptError(result.detail)
    if attempt.status == "prepared":
        transition_attempt(session, attempt_id, "opened")
    elif attempt.status == "needs_input":
        # Re-opening after needs_input keeps needs_input unless moved explicitly.
        attempt.opened_at = attempt.opened_at or datetime.now(UTC)
        session.flush()
    return {
        "ok": True,
        "attempt_id": attempt.id,
        "status": attempt.status,
        "url": result.url,
        "execution_mode": attempt.execution_mode,
        "simplify_integration": False,
        "simplify_handoff": True,
        "instructions": SIMPLIFY_HANDOFF_INSTRUCTIONS,
        "detail": result.detail,
        "autofill_by_ganesha": False,
        "submitted": False,
    }


def fill_attempt_controlled_browser(
    session: Session,
    attempt_id: str,
    *,
    headless: bool = False,
    keep_open: bool = True,
) -> dict[str, Any]:
    """Mode B: fill supported Greenhouse fields, never submit."""
    from apps.application_execution.adapters.greenhouse import is_greenhouse_url
    from apps.application_execution.services.approved_profile import (
        load_approved_profile,
    )
    from apps.application_execution.services.browser_executor import AutofillRequest
    from apps.application_execution.services.playwright_executor import (
        PlaywrightGreenhouseExecutor,
    )
    from apps.application_execution.paths import EXECUTION_MODE_CONTROLLED

    attempt = get_attempt(session, attempt_id)
    if attempt.status in {"cancelled", "submitted_user_confirmed", "failed"}:
        raise AttemptError(f"Cannot autofill attempt in status {attempt.status}")
    if not is_greenhouse_url(attempt.application_url or ""):
        raise AttemptError(
            "Controlled autofill currently supports Greenhouse application URLs only."
        )

    package = attempt.package_json or {}
    resume = package.get("resume") if isinstance(package.get("resume"), dict) else {}
    resume_path = Path(str(resume.get("pdf_path") or resume.get("docx_path") or ""))
    if not resume_path.is_file() or resume_path.parent.name == "Desktop":
        raise AttemptError(
            "Job-specific resume artifact is missing or looks like a Desktop generic file."
        )
    cover_path = None
    if attempt.include_cover_letter:
        cover = (
            package.get("cover_letter")
            if isinstance(package.get("cover_letter"), dict)
            else None
        )
        if cover:
            cover_path = Path(str(cover.get("pdf_path") or cover.get("docx_path") or ""))
            if not cover_path.is_file():
                cover_path = None

    profile = load_approved_profile(session)
    executor = PlaywrightGreenhouseExecutor()
    result = executor.fill_application(
        AutofillRequest(
            application_url=attempt.application_url or "",
            resume_path=resume_path,
            cover_letter_path=cover_path,
            include_cover_letter=bool(attempt.include_cover_letter and cover_path),
            profile=profile,
            headless=headless,
            keep_open=keep_open and not headless,
        )
    )

    attempt.execution_mode = EXECUTION_MODE_CONTROLLED
    package = dict(package)
    package["controlled_autofill"] = {
        "platform": result.platform,
        "ok": result.ok,
        "filled": [
            {
                "field_key": f.field_key,
                "label": f.label,
                "status": f.status,
                "detail": f.detail,
            }
            for f in result.filled
        ],
        "resume_uploaded": result.resume_uploaded,
        "cover_uploaded": result.cover_uploaded,
        "blockers": result.blockers,
        "submitted": False,
        "paused_for_review": result.paused_for_review,
    }
    package["ganesha_autofill"] = True
    package["simplify_mode"] = "user_driven_handoff_still_available"
    attempt.package_json = package
    if result.unresolved:
        attempt.unresolved_json = result.unresolved
    if result.safe_error:
        attempt.safe_error = result.safe_error
        transition_attempt(session, attempt_id, "failed", note=result.safe_error)
    elif any(
        u.get("kind") == "sensitive_question" or u.get("kind") == "browser_blocker"
        for u in result.unresolved
    ):
        transition_attempt(session, attempt_id, "needs_input")
    else:
        transition_attempt(session, attempt_id, "ready_for_review")
    session.flush()
    _write_package_sidecar(attempt)
    return {
        "ok": result.ok,
        "attempt": attempt_public_dict(attempt),
        "filled": package["controlled_autofill"]["filled"],
        "unresolved": attempt.unresolved_json,
        "blockers": result.blockers,
        "resume_uploaded": result.resume_uploaded,
        "cover_uploaded": result.cover_uploaded,
        "paused_for_review": result.paused_for_review,
        "submitted": False,
        "autofill_by_ganesha": True,
        "simplify_handoff_still_available": True,
        "safe_error": result.safe_error,
        "platform": result.platform,
    }


def reveal_attempt_documents(session: Session, attempt_id: str) -> dict[str, Any]:
    attempt = get_attempt(session, attempt_id)
    package = attempt.package_json or {}
    revealed: list[dict[str, str]] = []
    for key in ("resume", "cover_letter"):
        art = package.get(key)
        if not isinstance(art, dict):
            continue
        for path_key in ("pdf_path", "docx_path"):
            path = art.get(path_key)
            if isinstance(path, str) and path and not path.endswith("Desktop.pdf"):
                # Extra guard: never reveal a Desktop-only generic file as selected.
                if Path(path).parent.name == "Desktop":
                    continue
                outcome = reveal_local_path(path)
                revealed.append(
                    {
                        "kind": key,
                        "path": path,
                        "ok": str(outcome.ok),
                        "detail": outcome.detail,
                    }
                )
                break
    return {
        "attempt_id": attempt.id,
        "revealed": revealed,
        "note": "Local filesystem reveal only; not a cloud download API.",
    }


def attempt_public_dict(attempt: ApplicationAttempt) -> dict[str, Any]:
    from apps.application_execution.services.chrome_handoff import (
        SIMPLIFY_HANDOFF_INSTRUCTIONS,
    )

    package = dict(attempt.package_json or {})
    # Always surface current honest handoff copy (not a stale persisted phrase).
    package["instructions"] = list(SIMPLIFY_HANDOFF_INSTRUCTIONS)
    package["simplify_mode"] = "user_driven_handoff"
    package["ganesha_autofill"] = False
    return {
        "attempt_id": attempt.id,
        "job_id": attempt.job_posting_id,
        "company": attempt.company_name,
        "role": attempt.role_title,
        "posting_url": attempt.posting_url,
        "application_url": attempt.application_url,
        "execution_mode": attempt.execution_mode,
        "status": attempt.status,
        "include_cover_letter": attempt.include_cover_letter,
        "cover_letter_optional": attempt.cover_letter_optional,
        "resume_generation_id": attempt.resume_generation_id,
        "cover_letter_stamp": attempt.cover_letter_stamp,
        "profile_data_ref": attempt.profile_data_ref,
        "package": package,
        "unresolved": attempt.unresolved_json,
        "safe_error": attempt.safe_error,
        "prepared_at": attempt.prepared_at.isoformat() if attempt.prepared_at else None,
        "opened_at": attempt.opened_at.isoformat() if attempt.opened_at else None,
        "submitted_user_confirmed_at": (
            attempt.submitted_user_confirmed_at.isoformat()
            if attempt.submitted_user_confirmed_at
            else None
        ),
        "simplify_integration": False,
        "autofill_by_ganesha": bool(
            (attempt.package_json or {}).get("controlled_autofill")
        ),
        "controlled_autofill_supported": (
            "greenhouse.io" in (attempt.application_url or "").lower()
            or "greenhouse.com" in (attempt.application_url or "").lower()
        ),
        "api_key_required": False,
    }
