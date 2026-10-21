from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from apps.dashboard.app import create_app
from apps.resume_generation.schemas import GenerationResult, ValidationReport
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
def client() -> TestClient:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        session.query(JobPosting).delete()
        session.query(Company).delete()
        session.commit()
    finally:
        session.close()
    return TestClient(create_app())


def _seed(description: str = "Backend Python API software engineer role " * 15) -> str:
    session = SessionLocal()
    try:
        company = Company(canonical_name="DashCo", normalized_key="dashco")
        session.add(company)
        session.flush()
        job_id = make_uuid()
        session.add(
            JobPosting(
                id=job_id,
                source="csv_import",
                source_name="linkedin",
                source_type="csv_import",
                title="Software Engineer",
                company_id=company.id,
                location="Remote",
                description_text=description,
                description_hash=make_description_hash(description),
                dedupe_key=make_dedupe_key("DashCo", "Software Engineer", "Remote"),
                company_apply_url="https://example.com/jobs/1",
                status="new",
                scraped_at=datetime.now(UTC),
                discovered_at=datetime.now(UTC),
                source_import_key=f"resume-dash|{make_uuid()}",
            )
        )
        session.commit()
        return job_id
    finally:
        session.close()


def test_feed_does_not_show_generate_action(client: TestClient) -> None:
    _seed()
    page = client.get("/")
    assert page.status_code == 200
    assert "Generate tailored resume" not in page.text
    assert 'id="actions-toggle"' not in page.text


def test_job_detail_has_actions_dropdown(client: TestClient) -> None:
    job_id = _seed()
    page = client.get(f"/jobs/{job_id}")
    assert page.status_code == 200
    assert "Actions" in page.text
    assert "Generate tailored resume" in page.text
    assert 'id="actions-toggle"' in page.text
    assert 'id="action-generate-resume"' in page.text


def test_incomplete_description_warning(client: TestClient) -> None:
    job_id = _seed(description="Short")
    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["description_incomplete"] is True


def test_successful_generation_payload(client: TestClient) -> None:
    job_id = _seed()
    result = GenerationResult(
        status="succeeded",
        generation_id="gen-1",
        track="dev",
        docx_path="/tmp/out.docx",
        pdf_path="/tmp/out.pdf",
        desktop_pdf_path="/Users/amanjakunnel/Desktop/Alex_Michael_Manjakunnel_Resume.pdf",
        source_files=["Current_Resume_Dev.docx"],
        warnings=[],
        gaps=["No verified evidence for keyword: kubernetes"],
        provider="deterministic",
        validation=ValidationReport(
            ok=True,
            page_count=1,
            docx_ok=True,
            pdf_ok=True,
            text_ok=True,
            messages=["ok"],
        ),
    )
    with patch(
        "apps.dashboard.routes.generate_resume_for_job",
        return_value=result,
    ):
        response = client.post(
            f"/api/jobs/{job_id}/resume/generate",
            json={"confirm": True, "deterministic_only": True},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["desktop_pdf_path"].endswith("Alex_Michael_Manjakunnel_Resume.pdf")
    assert "gaps" in body


def test_job_list_omits_private_resume_metadata(client: TestClient) -> None:
    _seed()
    response = client.get("/api/jobs")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert "gaps" not in item
    assert "fact_ids" not in item
    assert "warnings" not in item
    assert "authorization" not in item
