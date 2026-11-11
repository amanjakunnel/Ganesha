from __future__ import annotations

from pathlib import Path

from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import Base, SimplifyHistoryRecord
from packages.core.services.importers.simplify_tracker_csv import (
    import_simplify_tracker_csv,
)
from packages.core.services.status_precedence import (
    prefer_status,
    would_downgrade,
)


def test_applied_never_downgraded_by_saved() -> None:
    assert prefer_status("applied", "saved_only") == "applied"
    assert would_downgrade("applied", "saved_only") is True
    assert prefer_status("saved_only", "applied") == "applied"


def test_reimport_idempotent_and_no_saved_downgrade(tmp_path: Path) -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        url = "https://careers.example.com/jobs/precedence-1"
        csv_path = tmp_path / "tracker.csv"
        csv_path.write_text(
            "Job Title,Company Name,Location,Job URL,Applied Date,Status,Status Date,"
            "Archived,Date Archived,Notes,job_type\n"
            f"Eng,ExampleCo,Remote,{url},2026-11-01,APPLIED,2026-11-01,No,N/A,,Full-time\n"
            f"Eng,ExampleCo,Remote,{url},,SAVED,2026-11-02,No,N/A,,Full-time\n",
            encoding="utf-8",
        )
        archive = tmp_path / "archive"
        first = import_simplify_tracker_csv(
            session, csv_path, dry_run=False, archive_dir=archive
        )
        session.commit()
        assert first.applied_like >= 1

        statuses = {
            r.status_mapped
            for r in session.query(SimplifyHistoryRecord).filter(
                SimplifyHistoryRecord.job_url == url
            )
        }
        assert "applied" in statuses

        second = import_simplify_tracker_csv(
            session, csv_path, dry_run=False, archive_dir=archive
        )
        session.commit()
        assert second.new_records == 0
        assert second.already_imported >= 1

        # Highest status for the posting remains applied.
        best = None
        for r in session.query(SimplifyHistoryRecord).filter(
            SimplifyHistoryRecord.job_url == url
        ):
            best = prefer_status(best, r.status_mapped)
        assert best == "applied"
    finally:
        session.close()
