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


def _seed(
    *,
    title: str = "Software Engineer",
    company_name: str | None = "Acme",
    location: str | None = "Austin, TX",
    source_name: str = "linkedin",
    status: str = "new",
    apply_url: str | None = "https://jobs.example.com/1",
    scraped_at: datetime | None = None,
) -> JobPosting:
    session = SessionLocal()
    try:
        company = None
        if company_name:
            company = (
                session.query(Company)
                .filter(Company.canonical_name == company_name)
                .one_or_none()
            )
            if company is None:
                company = Company(
                    canonical_name=company_name, normalized_key=company_name.lower()
                )
                session.add(company)
                session.flush()
        now = scraped_at or datetime.now(UTC)
        job = JobPosting(
            id=make_uuid(),
            source="csv_import",
            source_name=source_name,
            source_type="csv_import",
            title=title,
            company_id=company.id if company else None,
            location=location,
            description_text=f"Body for {title}",
            description_hash=make_description_hash(f"Body for {title}"),
            dedupe_key=make_dedupe_key(company_name or "none", title, location),
            company_apply_url=apply_url,
            status=status,
            scraped_at=now,
            discovered_at=now,
            source_import_key=f"api-test|{make_uuid()}",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job
    finally:
        session.close()


def test_api_returns_all_jobs_including_non_actionable(client: TestClient) -> None:
    _seed(title="New Job", status="new")
    _seed(title="Skipped Job", status="skipped", company_name="Beta")
    response = client.get("/api/jobs")
    assert response.status_code == 200
    data = response.json()
    titles = {item["title"] for item in data["items"]}
    assert titles == {"New Job", "Skipped Job"}
    assert data["total"] == 2


def test_api_keyword_and_source_filters(client: TestClient) -> None:
    _seed(title="Backend Engineer", source_name="linkedin")
    _seed(title="Frontend Engineer", source_name="simplifyjobs", company_name="Other")
    response = client.get("/api/jobs", params={"q": "backend", "source": "linkedin"})
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Backend Engineer"
    assert data["items"][0]["job_url"] == "https://jobs.example.com/1"


def test_api_missing_url_is_null(client: TestClient) -> None:
    _seed(title="No URL", apply_url=None)
    response = client.get("/api/jobs")
    assert response.status_code == 200
    assert response.json()["items"][0]["job_url"] is None


def test_api_unknown_sort_rejected(client: TestClient) -> None:
    response = client.get("/api/jobs", params={"sort": "not_a_sort"})
    assert response.status_code == 422


def test_api_pagination_metadata(client: TestClient) -> None:
    for i in range(3):
        _seed(title=f"P{i}", company_name=f"Co{i}")
    response = client.get("/api/jobs", params={"page": 1, "page_size": 2})
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert data["total"] == 3
    assert data["total_pages"] == 2
    assert len(data["items"]) == 2


def test_api_iso_timestamps(client: TestClient) -> None:
    _seed(title="TS Job")
    item = client.get("/api/jobs").json()["items"][0]
    assert "T" in item["date_found"]
    assert item["created_at"] is None or "T" in item["created_at"]


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "password" not in response.text.lower()
    assert "postgres" not in response.text.lower() or "reachable" in response.text


def test_api_db_failure_hides_secrets(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import OperationalError

    def _raise(*_a: object, **_k: object) -> None:
        raise OperationalError(
            "SELECT 1", {}, Exception("password authentication failed")
        )

    monkeypatch.setattr("apps.dashboard.routes.list_dashboard_jobs", _raise)
    response = client.get("/api/jobs")
    assert response.status_code == 503
    text = response.text.lower()
    assert "password" not in text
    assert "traceback" not in text
    assert "db doctor" in text
