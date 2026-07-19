from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from packages.core.db import SessionLocal
from packages.core.domain.models import JobPosting

app = FastAPI(title="Ganesha Job Intake API")


class JobOut(BaseModel):
    id: str
    title: str
    company: str | None
    status: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/jobs", response_model=list[JobOut])
def get_jobs() -> list[JobOut]:
    session = SessionLocal()
    try:
        rows = session.query(JobPosting).order_by(JobPosting.created_at.desc()).limit(100).all()
        out: list[JobOut] = []
        for r in rows:
            out.append(JobOut(id=r.id, title=r.title, company=(r.company.canonical_name if r.company else None), status=r.status))
        return out
    finally:
        session.close()


@app.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str) -> JobOut:
    session = SessionLocal()
    try:
        r = session.query(JobPosting).get(job_id)
        if not r:
            raise HTTPException(status_code=404, detail="Not found")
        return JobOut(id=r.id, title=r.title, company=(r.company.canonical_name if r.company else None), status=r.status)
    finally:
        session.close()


@app.get("/review-queue", response_model=list[JobOut])
def review_queue() -> list[JobOut]:
    session = SessionLocal()
    try:
        rows = (
            session.query(JobPosting).filter(JobPosting.status.in_(["queued_for_review", "new"]))
        ).all()
        return [JobOut(id=r.id, title=r.title, company=(r.company.canonical_name if r.company else None), status=r.status) for r in rows]
    finally:
        session.close()
