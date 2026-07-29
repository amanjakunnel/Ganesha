from __future__ import annotations

from pathlib import Path

from collections.abc import Iterator

import pytest

from packages.core.domain.models import Base
from packages.core.db import SessionLocal, get_engine
from packages.core.services.company_normalize import is_target_company, normalize_company_key
from packages.core.services.importers.linkedin_csv import parse_linkedin_row
from packages.core.services.importers.symplicity_xlsx import _read_symplicity_jobs
from packages.core.services.importers.referral_contacts_xlsx import import_referral_contacts_xlsx
from packages.core.services.importers.linkedin_csv import import_linkedin_csv
from packages.core.services.job_dedupe import find_duplicate_job
from packages.core.services.job_intake import upsert_job_from_import
from packages.core.services.job_ranking import rank_job


@pytest.fixture(autouse=True)
def _db_schema() -> Iterator[None]:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def test_linkedin_row_mapping() -> None:
    row = {
        "Title": "Backend Engineer",
        "Company Name": "Databricks",
        "Description": "Build APIs " * 20,
        "Detail URL": "https://linkedin.com/jobs/1",
        "Company Apply Url": "https://databricks.com/apply/1",
        "Location": "SF",
        "Created At": "2026-01-01T00:00:00.000Z",
        "Scraped At": "2026-01-02T00:00:00.000Z",
    }
    data, err = parse_linkedin_row(row)
    assert err is None
    assert data is not None
    assert data["company"] == "Databricks"
    assert data["source_import_key"]


def test_symplicity_example_file_parses_globalfoundries() -> None:
    path = Path("sheets/Symplicity Example.xlsx")
    if not path.is_file():
        pytest.skip("local symplicity sheet not present")
    jobs = _read_symplicity_jobs(path)
    assert len(jobs) >= 1
    job = jobs[0]
    assert "AI/ML" in job["title"]
    assert job["company"] == "GlobalFoundries"
    assert "early-career" in job.get("description_text", "").lower()


def test_referral_import_idempotent(tmp_path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Company", "Name", "Position", "Team", "Location(s)", "Alternate Location"])
    ws.append(["Databricks", "Alex Example", "Engineer", "Data", "SF", None])
    p = tmp_path / "refs.xlsx"
    wb.save(p)
    session = SessionLocal()
    try:
        r1 = import_referral_contacts_xlsx(session, str(p))
        r2 = import_referral_contacts_xlsx(session, str(p))
        session.commit()
        assert r1.created == 1
        assert r2.updated == 1
    finally:
        session.close()


def test_dedupe_by_import_key() -> None:
    session = SessionLocal()
    try:
        payload = {
            "title": "Engineer",
            "company": "Acme",
            "description_text": "python api backend " * 10,
            "source_import_key": "test-key-1",
        }
        upsert_job_from_import(session, payload, source_name="linkedin", source_type="csv_import")
        dup = find_duplicate_job(session, {**payload, "source_name": "linkedin"})
        assert dup.is_duplicate
        session.commit()
    finally:
        session.close()


def test_ranking_prefers_early_career_over_clearance_senior() -> None:
    good = rank_job(
        job_id="1",
        title="AI/ML Systems Engineer (2026 New College Graduate)",
        company_name="GlobalFoundries",
        description="early-career machine learning workload analysis",
        source_name="symplicity",
        posted_at=None,
        scraped_at=None,
        has_referral_contacts=False,
        referral_contact_count=0,
        status="new",
        intake_metadata=None,
    )
    bad = rank_job(
        job_id="2",
        title="Senior Software Engineer",
        company_name="Raytheon",
        description="active security clearance required for senior staff role",
        source_name="linkedin",
        posted_at=None,
        scraped_at=None,
        has_referral_contacts=False,
        referral_contact_count=0,
        status="new",
        intake_metadata=None,
    )
    assert good.score > bad.score
    assert any("early_career" in r for r in good.reasons)
    assert any("clearance" in r for r in bad.reasons)


def test_target_company_detection() -> None:
    ok, name = is_target_company("Amazon Web Services")
    assert ok and name == "Amazon"
    assert normalize_company_key("Databricks") == normalize_company_key("Databricks")


def test_linkedin_csv_idempotent_rerun() -> None:
    path = Path("sheets/JobsScraperForLinkedIn_13_2026-07-29.csv")
    if not path.is_file():
        pytest.skip("local linkedin csv not present")
    session = SessionLocal()
    try:
        first = import_linkedin_csv(session, str(path))
        session.commit()
        second = import_linkedin_csv(session, str(path))
        session.commit()
        assert first.created > 0
        assert second.duplicates >= first.created
    finally:
        session.close()
