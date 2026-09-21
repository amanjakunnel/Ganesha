from __future__ import annotations

from collections.abc import Generator
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from apps.dashboard.schemas import JobQueryParams, JobSort, JobsListResponse
from apps.dashboard.services.jobs_dashboard_service import (
    check_database,
    list_dashboard_jobs,
)
from packages.core.db import SessionLocal
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


@router.get("/", response_class=HTMLResponse)
def jobs_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"db_unavailable_message": DB_UNAVAILABLE_MESSAGE},
    )
