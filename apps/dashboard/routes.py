from __future__ import annotations

from collections.abc import Generator
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from apps.dashboard.schemas import JobQueryParams, JobSort, JobsListResponse
from apps.dashboard.services.jobs_dashboard_service import (
    check_database,
    list_dashboard_jobs,
    to_dashboard_item,
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
    sort: JobSort = Query(default=JobSort.date_desc),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> JobQueryParams:
    return JobQueryParams(
        q=q,
        source=source,
        location=location,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        page=page,
        page_size=page_size,
    )


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


@router.get("/", response_class=HTMLResponse)
def jobs_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"db_unavailable_message": DB_UNAVAILABLE_MESSAGE},
    )


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
