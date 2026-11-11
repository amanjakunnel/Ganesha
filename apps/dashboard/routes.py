from __future__ import annotations

from collections.abc import Generator
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from apps.dashboard.schemas import (
    FitFeedFilter,
    JobQueryParams,
    JobSort,
    JobsListResponse,
    RecencyFilter,
)
from apps.dashboard.services.jobs_dashboard_service import (
    check_database,
    list_dashboard_jobs,
    to_dashboard_item,
)
from packages.core.services.job_refresh_service import (
    get_refresh_status,
    start_refresh,
)
from apps.cover_letter_generation.services.cover_letter_preflight_service import (
    cover_letter_preflight,
)
from apps.resume_generation.services.generation_preflight import generation_preflight
from packages.core.services.evidence_fit_service import get_stored_fit
from packages.core.services.job_eligibility import resolve_job_eligibility
from packages.core.settings import settings
from apps.cover_letter_generation.services.cover_letter_generation_service import (
    generate_cover_letter_for_job,
)
from apps.cover_letter_generation.services.cover_letter_plan_service import (
    build_cover_letter_plan,
)
from apps.cover_letter_generation.services.cover_letter_provenance_service import (
    plan_to_public_dict,
)
from apps.resume_generation.services.generation_service import (
    generate_resume_for_job,
    preview_generation,
)
from apps.resume_generation.services.job_requirement_service import (
    extract_job_requirements,
)
from apps.resume_generation.services.semantic_provider import semantic_status
from packages.core.db import SessionLocal
from packages.core.domain.models import JobPosting
from packages.core.services.db_health import is_database_unavailable

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

DB_UNAVAILABLE_MESSAGE = (
    "Ganesha could not load jobs from the local database. "
    'Run "python -m apps.cli.main db doctor" and retry.'
)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _parse_query(
    q: str | None = Query(default=None),
    source: str | None = Query(default=None),
    location: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    fit: FitFeedFilter | None = Query(default=None),
    recency: RecencyFilter = Query(default=RecencyFilter.all),
    sort: JobSort = Query(default=JobSort.date_desc),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> JobQueryParams:
    default_fit = FitFeedFilter.recommended
    try:
        default_fit = FitFeedFilter(settings.fit_default_feed)
    except ValueError:
        default_fit = FitFeedFilter.recommended
    return JobQueryParams(
        q=q,
        source=source,
        location=location,
        date_from=date_from,
        date_to=date_to,
        fit=fit or default_fit,
        recency=recency,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.get("/api/jobs/refresh/status")
def api_jobs_refresh_status(
    session: Session = Depends(get_session),
) -> JSONResponse:
    try:
        check_database(session)
        return JSONResponse(get_refresh_status(session))
    except Exception as exc:
        if is_database_unavailable(exc):
            return JSONResponse(
                {"error": DB_UNAVAILABLE_MESSAGE},
                status_code=503,
            )
        raise


@router.post("/api/jobs/refresh")
def api_jobs_refresh(
    session: Session = Depends(get_session),
) -> JSONResponse:
    """Start a background source sync. Does not block the UI."""
    try:
        check_database(session)
    except Exception as exc:
        if is_database_unavailable(exc):
            return JSONResponse(
                {"error": DB_UNAVAILABLE_MESSAGE},
                status_code=503,
            )
        raise
    state, code = start_refresh()
    return JSONResponse(state.to_dict(), status_code=code)


@router.get("/health")
def health(session: Session = Depends(get_session)) -> JSONResponse:
    try:
        check_database(session)
        return JSONResponse({"status": "ok", "database": "reachable"})
    except Exception as exc:
        if is_database_unavailable(exc):
            return JSONResponse(
                {"status": "error", "database": "unavailable"},
                status_code=503,
            )
        return JSONResponse(
            {"status": "error", "database": "unavailable"},
            status_code=503,
        )


@router.get("/api/jobs", response_model=JobsListResponse)
def api_jobs(
    params: JobQueryParams = Depends(_parse_query),
    session: Session = Depends(get_session),
) -> JobsListResponse | JSONResponse:
    try:
        return list_dashboard_jobs(session, params)
    except Exception as exc:
        if is_database_unavailable(exc):
            return JSONResponse(
                {"error": DB_UNAVAILABLE_MESSAGE},
                status_code=503,
            )
        raise


@router.get("/api/jobs/{job_id}")
def api_job_detail(
    job_id: str,
    session: Session = Depends(get_session),
) -> JSONResponse:
    try:
        job = (
            session.query(JobPosting)
            .options(joinedload(JobPosting.company))
            .filter(JobPosting.id == job_id)
            .one_or_none()
        )
        if job is None:
            return JSONResponse({"error": "Job not found"}, status_code=404)
        item = to_dashboard_item(job)
        req = extract_job_requirements(job)
        sem = semantic_status()
        resume_block = generation_preflight(session, job)
        cover_block = cover_letter_preflight(session, job)
        fit = get_stored_fit(job) or {}
        eligibility = resolve_job_eligibility(job)
        return JSONResponse(
            {
                "job": item.model_dump(mode="json"),
                "description_text": job.description_text or "",
                "description_incomplete": req.insufficient_description,
                "recommended_track": req.role_family,
                "workplace_type": job.workplace_type,
                "canonical_url": job.canonical_url,
                "company_apply_url": job.company_apply_url,
                "semantic_provider": sem["provider"],
                "semantic_configured": sem["api_key"] == "configured"
                or sem["provider"] == "none",
                "eligibility": eligibility.result,
                "fit": fit,
                "resume_generation": {
                    "available": resume_block is None,
                    "block_reason": resume_block,
                },
                "cover_letter_generation": {
                    "available": cover_block is None,
                    "block_reason": cover_block,
                },
            }
        )
    except Exception as exc:
        if is_database_unavailable(exc):
            return JSONResponse(
                {"error": DB_UNAVAILABLE_MESSAGE},
                status_code=503,
            )
        raise


class ResumeGenerateBody(BaseModel):
    track: str | None = None
    deterministic_only: bool = False
    confirm: bool = False


@router.get("/api/jobs/{job_id}/resume/preview")
def api_resume_preview(
    job_id: str,
    session: Session = Depends(get_session),
) -> JSONResponse:
    try:
        preview = preview_generation(session, job_id)
        status = 404 if preview.get("error") == "Job not found" else 200
        return JSONResponse(preview, status_code=status)
    except Exception as exc:
        if is_database_unavailable(exc):
            return JSONResponse(
                {"error": DB_UNAVAILABLE_MESSAGE},
                status_code=503,
            )
        raise


@router.post("/api/jobs/{job_id}/resume/generate")
def api_resume_generate(
    job_id: str,
    body: ResumeGenerateBody,
    session: Session = Depends(get_session),
) -> JSONResponse:
    if not body.confirm:
        return JSONResponse(
            {"error": "Confirmation required", "status": "error"},
            status_code=400,
        )
    try:
        result = generate_resume_for_job(
            session,
            job_id,
            track=body.track,
            deterministic_only=body.deterministic_only,
            copy_to_desktop=True,
        )
        session.commit()
        payload = {
            "status": result.status,
            "generation_id": result.generation_id,
            "track": result.track,
            "docx_path": result.docx_path,
            "pdf_path": result.pdf_path,
            "desktop_pdf_path": result.desktop_pdf_path,
            "source_files": result.source_files,
            "warnings": result.warnings,
            "gaps": result.gaps,
            "provider": result.provider,
            "error": result.error,
            "validation": (
                {
                    "ok": result.validation.ok,
                    "page_count": result.validation.page_count,
                    "messages": result.validation.messages,
                }
                if result.validation
                else None
            ),
        }
        code = 200 if result.status == "succeeded" else 400
        return JSONResponse(payload, status_code=code)
    except Exception as exc:
        session.rollback()
        if is_database_unavailable(exc):
            return JSONResponse(
                {"error": DB_UNAVAILABLE_MESSAGE},
                status_code=503,
            )
        raise


class CoverLetterBody(BaseModel):
    voice: str = "technical_direct"
    deterministic_only: bool = False
    confirm: bool = False
    skip_company_research: bool = False


@router.get("/api/jobs/{job_id}/cover-letter/plan")
def api_cover_letter_plan(
    job_id: str,
    voice: str = "technical_direct",
    session: Session = Depends(get_session),
) -> JSONResponse:
    try:
        job = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
        if job is None:
            return JSONResponse({"error": "Job not found"}, status_code=404)
        plan = build_cover_letter_plan(
            session,
            job,
            voice=voice,
            skip_company_research=False,
        )
        return JSONResponse(plan_to_public_dict(plan))
    except Exception as exc:
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.post("/api/jobs/{job_id}/cover-letter/generate")
def api_cover_letter_generate(
    job_id: str,
    body: CoverLetterBody,
    session: Session = Depends(get_session),
) -> JSONResponse:
    if not body.confirm:
        return JSONResponse(
            {"error": "Confirmation required", "status": "error"},
            status_code=400,
        )
    try:
        result = generate_cover_letter_for_job(
            session,
            job_id,
            voice=body.voice,
            deterministic_only=body.deterministic_only,
            copy_to_desktop=True,
            skip_company_research=body.skip_company_research,
        )
        session.commit()
        payload = {
            "status": result.status,
            "voice": result.voice,
            "provider": result.provider,
            "evidence_count": result.evidence_count,
            "company_evidence_used": result.company_evidence_used,
            "docx_path": result.docx_path,
            "pdf_path": result.pdf_path,
            "desktop_pdf_path": result.desktop_pdf_path,
            "warnings": result.warnings,
            "error": result.error,
            "plan": result.plan,
            "validation": (
                {
                    "ok": result.validation.ok,
                    "page_count": result.validation.page_count,
                    "word_count": result.validation.word_count,
                    "messages": result.validation.messages,
                }
                if result.validation
                else None
            ),
        }
        code = 200 if result.status == "succeeded" else 400
        return JSONResponse(payload, status_code=code)
    except Exception as exc:
        session.rollback()
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


class PrepareApplicationBody(BaseModel):
    include_cover_letter: bool = False
    force_new: bool = False
    acknowledge_fit_warning: bool = False
    acknowledge_previous_application: bool = False
    confirm: bool = False


class AttemptActionBody(BaseModel):
    confirm: bool = False
    note: str | None = None


@router.get("/api/jobs/{job_id}/application/prepare-preview")
def api_application_prepare_preview(
    job_id: str,
    session: Session = Depends(get_session),
) -> JSONResponse:
    from apps.application_execution.services.attempt_service import prepare_preview
    from packages.core.services.job_service import JobNotFoundError

    try:
        preview = prepare_preview(session, job_id)
        return JSONResponse(preview.to_dict())
    except JobNotFoundError:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    except Exception as exc:
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.post("/api/jobs/{job_id}/application/prepare")
def api_application_prepare(
    job_id: str,
    body: PrepareApplicationBody,
    session: Session = Depends(get_session),
) -> JSONResponse:
    from apps.application_execution.services.attempt_service import (
        AttemptBlockedError,
        DuplicateActiveAttemptError,
        attempt_public_dict,
        prepare_attempt,
    )
    from packages.core.services.job_service import JobNotFoundError

    if not body.confirm:
        return JSONResponse(
            {"error": "Confirmation required", "status": "error"},
            status_code=400,
        )
    try:
        attempt = prepare_attempt(
            session,
            job_id,
            include_cover_letter=body.include_cover_letter,
            force_new=body.force_new,
            acknowledge_fit_warning=body.acknowledge_fit_warning,
            acknowledge_previous_application=body.acknowledge_previous_application,
        )
        session.commit()
        return JSONResponse(
            {"status": "prepared", "attempt": attempt_public_dict(attempt)}
        )
    except JobNotFoundError:
        session.rollback()
        return JSONResponse({"error": "Job not found"}, status_code=404)
    except DuplicateActiveAttemptError as exc:
        session.rollback()
        return JSONResponse(
            {
                "error": str(exc),
                "status": "duplicate_active",
                "active_attempts": [
                    {"attempt_id": a.id, "status": a.status} for a in exc.active
                ],
            },
            status_code=409,
        )
    except AttemptBlockedError as exc:
        session.rollback()
        return JSONResponse({"error": str(exc), "status": "blocked"}, status_code=400)
    except Exception as exc:
        session.rollback()
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.get("/api/jobs/{job_id}/application/attempts")
def api_application_attempts(
    job_id: str,
    session: Session = Depends(get_session),
) -> JSONResponse:
    from apps.application_execution.services.attempt_service import (
        attempt_public_dict,
        list_attempts_for_job,
    )

    try:
        rows = list_attempts_for_job(session, job_id)
        return JSONResponse(
            {"job_id": job_id, "attempts": [attempt_public_dict(r) for r in rows]}
        )
    except Exception as exc:
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.get("/api/application-attempts/{attempt_id}")
def api_application_attempt_detail(
    attempt_id: str,
    session: Session = Depends(get_session),
) -> JSONResponse:
    from apps.application_execution.services.attempt_service import (
        AttemptNotFoundError,
        attempt_public_dict,
        get_attempt,
    )

    try:
        return JSONResponse(attempt_public_dict(get_attempt(session, attempt_id)))
    except AttemptNotFoundError:
        return JSONResponse({"error": "Attempt not found"}, status_code=404)
    except Exception as exc:
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.post("/api/application-attempts/{attempt_id}/open")
def api_application_attempt_open(
    attempt_id: str,
    body: AttemptActionBody,
    session: Session = Depends(get_session),
) -> JSONResponse:
    from apps.application_execution.services.attempt_service import (
        AttemptError,
        AttemptNotFoundError,
        open_attempt_in_chrome,
    )

    if not body.confirm:
        return JSONResponse({"error": "Confirmation required"}, status_code=400)
    try:
        payload = open_attempt_in_chrome(session, attempt_id)
        session.commit()
        return JSONResponse(payload)
    except AttemptNotFoundError:
        session.rollback()
        return JSONResponse({"error": "Attempt not found"}, status_code=404)
    except AttemptError as exc:
        session.rollback()
        return JSONResponse({"error": str(exc), "status": "error"}, status_code=400)
    except Exception as exc:
        session.rollback()
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.post("/api/application-attempts/{attempt_id}/fill-supported")
def api_application_attempt_fill_supported(
    attempt_id: str,
    body: AttemptActionBody,
    session: Session = Depends(get_session),
) -> JSONResponse:
    from apps.application_execution.services.attempt_service import (
        AttemptError,
        AttemptNotFoundError,
        fill_attempt_controlled_browser,
    )

    if not body.confirm:
        return JSONResponse({"error": "Confirmation required"}, status_code=400)
    try:
        payload = fill_attempt_controlled_browser(
            session,
            attempt_id,
            headless=False,
            keep_open=True,
        )
        session.commit()
        return JSONResponse(payload)
    except AttemptNotFoundError:
        session.rollback()
        return JSONResponse({"error": "Attempt not found"}, status_code=404)
    except AttemptError as exc:
        session.rollback()
        return JSONResponse({"error": str(exc), "status": "error"}, status_code=400)
    except Exception as exc:
        session.rollback()
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.post("/api/application-attempts/{attempt_id}/reveal-documents")
def api_application_attempt_reveal(
    attempt_id: str,
    body: AttemptActionBody,
    session: Session = Depends(get_session),
) -> JSONResponse:
    from apps.application_execution.services.attempt_service import (
        AttemptNotFoundError,
        reveal_attempt_documents,
    )

    if not body.confirm:
        return JSONResponse({"error": "Confirmation required"}, status_code=400)
    try:
        payload = reveal_attempt_documents(session, attempt_id)
        return JSONResponse(payload)
    except AttemptNotFoundError:
        return JSONResponse({"error": "Attempt not found"}, status_code=404)
    except Exception as exc:
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.post("/api/application-attempts/{attempt_id}/mark-submitted")
def api_application_attempt_mark_submitted(
    attempt_id: str,
    body: AttemptActionBody,
    session: Session = Depends(get_session),
) -> JSONResponse:
    from apps.application_execution.services.attempt_service import (
        AttemptError,
        AttemptNotFoundError,
        attempt_public_dict,
        transition_attempt,
    )

    if not body.confirm:
        return JSONResponse({"error": "Confirmation required"}, status_code=400)
    try:
        attempt = transition_attempt(
            session,
            attempt_id,
            "submitted_user_confirmed",
            note=body.note,
        )
        session.commit()
        return JSONResponse(
            {
                "status": "submitted_user_confirmed",
                "attempt": attempt_public_dict(attempt),
                "inferred_from_browser": False,
            }
        )
    except AttemptNotFoundError:
        session.rollback()
        return JSONResponse({"error": "Attempt not found"}, status_code=404)
    except AttemptError as exc:
        session.rollback()
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        session.rollback()
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.post("/api/application-attempts/{attempt_id}/mark-needs-action")
def api_application_attempt_needs_action(
    attempt_id: str,
    body: AttemptActionBody,
    session: Session = Depends(get_session),
) -> JSONResponse:
    from apps.application_execution.services.attempt_service import (
        AttemptError,
        AttemptNotFoundError,
        attempt_public_dict,
        transition_attempt,
    )

    if not body.confirm:
        return JSONResponse({"error": "Confirmation required"}, status_code=400)
    try:
        attempt = transition_attempt(
            session, attempt_id, "needs_input", note=body.note
        )
        session.commit()
        return JSONResponse(
            {"status": "needs_input", "attempt": attempt_public_dict(attempt)}
        )
    except AttemptNotFoundError:
        session.rollback()
        return JSONResponse({"error": "Attempt not found"}, status_code=404)
    except AttemptError as exc:
        session.rollback()
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        session.rollback()
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.post("/api/application-attempts/{attempt_id}/cancel")
def api_application_attempt_cancel(
    attempt_id: str,
    body: AttemptActionBody,
    session: Session = Depends(get_session),
) -> JSONResponse:
    from apps.application_execution.services.attempt_service import (
        AttemptError,
        AttemptNotFoundError,
        attempt_public_dict,
        transition_attempt,
    )

    if not body.confirm:
        return JSONResponse({"error": "Confirmation required"}, status_code=400)
    try:
        attempt = transition_attempt(
            session, attempt_id, "cancelled", note=body.note
        )
        session.commit()
        return JSONResponse(
            {"status": "cancelled", "attempt": attempt_public_dict(attempt)}
        )
    except AttemptNotFoundError:
        session.rollback()
        return JSONResponse({"error": "Attempt not found"}, status_code=404)
    except AttemptError as exc:
        session.rollback()
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        session.rollback()
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


class LikelyAppliedActionBody(BaseModel):
    record_ids: list[str]
    action: str  # confirm | reject


@router.get("/api/likely-applied")
def api_likely_applied(
    session: Session = Depends(get_session),
) -> JSONResponse:
    from packages.core.services.likely_applied_service import list_likely_applied
    from packages.core.services.gmail_application_match import gmail_ready, gmail_paths

    try:
        check_database(session)
        items = [c.to_dict() for c in list_likely_applied(session)]
        return JSONResponse(
            {
                "items": items,
                "count": len(items),
                "low_confidence": sum(1 for i in items if i.get("low_confidence")),
                "gmail_ready": gmail_ready(),
                "gmail_paths": gmail_paths(),
            }
        )
    except Exception as exc:
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.post("/api/likely-applied/action")
def api_likely_applied_action(
    body: LikelyAppliedActionBody,
    session: Session = Depends(get_session),
) -> JSONResponse:
    from packages.core.services.likely_applied_service import (
        confirm_likely_applied,
        reject_likely_applied,
    )

    try:
        check_database(session)
        if body.action == "confirm":
            result = confirm_likely_applied(session, body.record_ids)
        elif body.action == "reject":
            result = reject_likely_applied(session, body.record_ids)
        else:
            return JSONResponse({"error": "action must be confirm or reject"}, status_code=400)
        session.commit()
        return JSONResponse(result)
    except Exception as exc:
        session.rollback()
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.post("/api/likely-applied/gmail-preview")
def api_gmail_preview(
    session: Session = Depends(get_session),
) -> JSONResponse:
    """Preview Gmail confirmation matches without writing (unless client_secret present)."""
    from packages.core.services.gmail_application_match import preview_gmail_matches

    try:
        check_database(session)
        preview = preview_gmail_matches(session, persist=False)
        return JSONResponse(preview.to_dict())
    except Exception as exc:
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.post("/api/likely-applied/gmail-apply")
def api_gmail_apply(
    session: Session = Depends(get_session),
) -> JSONResponse:
    """Persist Gmail match evidence onto saved-only records (still never auto-promotes)."""
    from packages.core.services.gmail_application_match import preview_gmail_matches

    try:
        check_database(session)
        preview = preview_gmail_matches(session, persist=True)
        if preview.setup_required:
            return JSONResponse(preview.to_dict(), status_code=400)
        session.commit()
        return JSONResponse(preview.to_dict())
    except Exception as exc:
        session.rollback()
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.get("/", response_class=HTMLResponse)
def jobs_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"db_unavailable_message": DB_UNAVAILABLE_MESSAGE},
    )


@router.get("/likely-applied", response_class=HTMLResponse)
def likely_applied_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "likely_applied.html",
        {"db_unavailable_message": DB_UNAVAILABLE_MESSAGE},
    )


class AnswerBankBody(BaseModel):
    question_pattern: str
    answer: str
    scope: str = "global"
    company: str | None = None
    category: str | None = None


@router.get("/api/answer-bank")
def api_answer_bank_list() -> JSONResponse:
    from packages.core.services.answer_bank import list_entries

    items = [e.to_public_dict() for e in list_entries()]
    return JSONResponse({"items": items, "count": len(items)})


@router.post("/api/answer-bank")
def api_answer_bank_add(body: AnswerBankBody) -> JSONResponse:
    from packages.core.services.answer_bank import add_entry

    try:
        entry = add_entry(
            question_pattern=body.question_pattern,
            answer=body.answer,
            scope=body.scope if body.scope in {"global", "company"} else "global",  # type: ignore[arg-type]
            company=body.company,
            category=body.category,
        )
        return JSONResponse({"entry": entry.to_public_dict()}, status_code=201)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.delete("/api/answer-bank/{entry_id}")
def api_answer_bank_delete(entry_id: str) -> JSONResponse:
    from packages.core.services.answer_bank import delete_entry

    ok = delete_entry(entry_id)
    if not ok:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"deleted": True})


@router.get("/answer-bank", response_class=HTMLResponse)
def answer_bank_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "answer_bank.html",
        {"db_unavailable_message": DB_UNAVAILABLE_MESSAGE},
    )


@router.get("/api/review-queue")
def api_review_queue(session: Session = Depends(get_session)) -> JSONResponse:
    from packages.core.domain.models import ApplicationAttempt

    try:
        check_database(session)
        rows = (
            session.query(ApplicationAttempt)
            .filter(ApplicationAttempt.status.in_(["ready_for_review", "needs_input"]))
            .order_by(ApplicationAttempt.updated_at.desc())
            .limit(100)
            .all()
        )
        items = []
        for row in rows:
            unresolved = []
            payload = row.package_json or {}
            if isinstance(payload, dict):
                unresolved = (
                    (payload.get("controlled_autofill") or {}).get("unresolved")
                    or payload.get("unresolved")
                    or []
                )
            items.append(
                {
                    "attempt_id": row.id,
                    "job_id": row.job_posting_id,
                    "company": row.company_name,
                    "role": row.role_title,
                    "status": row.status,
                    "fields_left": unresolved,
                    "application_url": row.application_url,
                }
            )
        return JSONResponse({"items": items, "count": len(items)})
    except Exception as exc:
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.get("/review-queue", response_class=HTMLResponse)
def review_queue_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "review_queue.html",
        {"db_unavailable_message": DB_UNAVAILABLE_MESSAGE},
    )


@router.post("/api/jobs/{job_id}/liveness")
def api_job_liveness(
    job_id: str,
    session: Session = Depends(get_session),
) -> JSONResponse:
    from packages.core.domain.models import JobPosting
    from packages.core.services.job_liveness import check_job_liveness

    try:
        job = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
        if job is None:
            return JSONResponse({"error": "Job not found"}, status_code=404)
        result = check_job_liveness(session, job, mark_closed=True)
        session.commit()
        return JSONResponse(result.to_dict())
    except Exception as exc:
        session.rollback()
        if is_database_unavailable(exc):
            return JSONResponse({"error": DB_UNAVAILABLE_MESSAGE}, status_code=503)
        raise


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail_page(request: Request, job_id: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "db_unavailable_message": DB_UNAVAILABLE_MESSAGE,
            "job_id": job_id,
        },
    )
