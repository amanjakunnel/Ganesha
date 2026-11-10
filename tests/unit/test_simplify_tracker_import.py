from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import (
    Base,
    Company,
    JobPosting,
    SimplifyHistoryRecord,
    make_dedupe_key,
    make_description_hash,
    make_uuid,
)
from packages.core.services.importers.simplify_tracker_csv import (
    canonical_job_url,
    import_simplify_tracker_csv,
    map_simplify_status,
    previously_applied_for_job,
)


def _session() -> Session:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def test_status_mapping_saved_is_not_applied() -> None:
    raw, mapped = map_simplify_status("SAVED")
    assert mapped == "saved_only"
    raw2, mapped2 = map_simplify_status("APPLIED")
    assert mapped2 == "applied"
    raw3, mapped3 = map_simplify_status("WEIRD")
    assert mapped3 is None


def test_import_dedupe_idempotent_and_match(tmp_path: Path) -> None:
    session = _session()
    try:
        company = Company(canonical_name="Google", normalized_key="google")
        session.add(company)
        session.flush()
        url = "https://careers.google.com/jobs/results/123"
        job = JobPosting(
            id=make_uuid(),
            source="test",
            title="Software Engineer II",
            company_id=company.id,
            location="N/A",
            description_text="x" * 100,
            description_hash=make_description_hash("x" * 100),
            dedupe_key=make_dedupe_key("Google", "Software Engineer II", "N/A"),
            status="new",
            discovered_at=datetime.now(UTC),
            company_apply_url=url + "?utm_source=Simplify",
            source_import_key=f"simp-test|{make_uuid()}",
        )
        session.add(job)
        session.commit()

        csv_path = tmp_path / "tracker.csv"
        csv_path.write_text(
            "Job Title,Company Name,Location,Job URL,Applied Date,Status,Status Date,"
            "Archived,Date Archived,Notes,job_type\n"
            f"Software Engineer II,Google,N/A,{url}?utm=1,2026-11-03,APPLIED,2026-11-03,No,N/A,,Full-time\n"
            f"Software Engineer II,Google,N/A,{url}?utm=2,2026-11-03,APPLIED,2026-11-03,No,N/A,,Full-time\n"
            "Other Role,Acme,N/A,https://example.com/a,2026-11-01,SAVED,2026-11-01,No,N/A,,Full-time\n",
            encoding="utf-8",
        )
        archive = tmp_path / "archive"
        preview = import_simplify_tracker_csv(
            session, csv_path, dry_run=True, archive_dir=archive
        )
        assert preview.rows_read == 3
        assert preview.duplicates_collapsed == 1
        assert preview.matched_existing_jobs >= 1
        assert preview.saved_only >= 1
        assert preview.applied_like >= 1

        applied = import_simplify_tracker_csv(
            session, csv_path, dry_run=False, archive_dir=archive
        )
        session.commit()
        assert applied.new_records >= 2
        again = import_simplify_tracker_csv(
            session, csv_path, dry_run=False, archive_dir=archive
        )
        session.commit()
        assert again.already_imported >= 2
        assert again.new_records == 0
        assert session.query(SimplifyHistoryRecord).count() >= 2

        session.refresh(job)
        warning = previously_applied_for_job(session, job)
        assert warning is not None
        assert warning["requires_explicit_continue"] is True
        assert canonical_job_url(url + "?x=1") == canonical_job_url(url)
    finally:
        session.close()
