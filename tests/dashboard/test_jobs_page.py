from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from apps.dashboard.app import create_app
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


def _seed(*, title: str, apply_url: str | None) -> None:
    session = SessionLocal()
    try:
        company = Company(canonical_name="PageCo", normalized_key="pageco")
        session.add(company)
        session.flush()
        now = datetime.now(UTC)
        session.add(
            JobPosting(
                id=make_uuid(),
                source="csv_import",
                source_name="linkedin",
                source_type="csv_import",
                title=title,
                company_id=company.id,
                location="Remote",
                description_text="desc",
                description_hash=make_description_hash("desc"),
                dedupe_key=make_dedupe_key("PageCo", title, "Remote"),
                company_apply_url=apply_url,
                status="new",
                scraped_at=now,
                discovered_at=now,
                source_import_key=f"page-test|{make_uuid()}",
            )
        )
        session.commit()
    finally:
        session.close()


def test_page_renders_controls(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "Ganesha Job Dashboard" in html
    assert 'id="q"' in html
    assert 'id="source"' in html
    assert 'id="location"' in html
    assert 'id="date_from"' in html
    assert 'id="date_to"' in html
    assert 'id="sort"' in html
    assert 'id="clear-filters"' in html
    assert "/static/dashboard.css" in html
    assert "/static/dashboard.js" in html
    assert "db doctor" in html


def test_page_js_marks_unavailable_and_safe_links(client: TestClient) -> None:
    js = client.get("/static/dashboard.js")
    assert js.status_code == 200
    text = js.text
    assert "noopener noreferrer" in text
    assert "Unavailable" in text
    assert 'target="_blank"' in text or "target='_blank'" in text
    assert "Open posting" in text


def test_api_empty_state_for_page_data(client: TestClient) -> None:
    response = client.get("/api/jobs")
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_api_with_url_for_page_consumption(client: TestClient) -> None:
    _seed(title="Linked Role", apply_url="https://jobs.example.com/open")
    data = client.get("/api/jobs").json()
    assert data["items"][0]["job_url"] == "https://jobs.example.com/open"
    assert data["items"][0]["source_label"] == "linkedin_import"
