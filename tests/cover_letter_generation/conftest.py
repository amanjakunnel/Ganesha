from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import (
    Base,
    Company,
    JobPosting,
    make_dedupe_key,
    make_description_hash,
    make_uuid,
)


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


def long_description(*extra: str) -> str:
    base = (
        "Software engineer role building Python services, AWS infrastructure, "
        "Kubernetes deployments, Terraform modules, and Jenkins pipelines for "
        "reliable production systems. "
    )
    text = (base + " ".join(extra)).strip()
    while len(text) < 450:
        text += " " + base
    return text


def add_job(
    session: Session,
    *,
    title: str = "Software Engineer",
    company_name: str = "Northwind Labs",
    description: str | None = None,
    url: str | None = "https://northwind.example/jobs/1",
    status: str = "new",
    domain: str | None = None,
) -> JobPosting:
    company = Company(
        canonical_name=company_name,
        normalized_key=make_uuid()[:8],
        website_domain=domain,
    )
    session.add(company)
    session.flush()
    text = description if description is not None else long_description()
    job = JobPosting(
        id=make_uuid(),
        source="csv_import",
        source_name="manual",
        source_type="csv_import",
        title=title,
        company_id=company.id,
        location="State College, PA",
        description_text=text,
        description_hash=make_description_hash(text),
        dedupe_key=make_dedupe_key(company_name, title, "State College, PA"),
        company_apply_url=url,
        status=status,
        scraped_at=datetime.now(UTC),
        discovered_at=datetime.now(UTC),
        source_import_key=f"cover|{make_uuid()}",
    )
    session.add(job)
    session.commit()
    return job


def blank_pdf(path: Path, *, pages: int = 1) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        writer.write(handle)
    return path
