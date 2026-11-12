"""Ambiguous duplicate matches must not raise MultipleResultsFound."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import (
    Base,
    JobPosting,
    make_dedupe_key,
    make_uuid,
)
from packages.core.services.job_dedupe import find_duplicate_job
from packages.core.services.job_intake import upsert_job_from_import


def test_find_duplicate_job_picks_earliest_on_ambiguous_dedupe_key() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    suffix = make_uuid()[:8]
    company = f"Anduril-{suffix}"
    title = f"Engineer-{suffix}"
    key = make_dedupe_key(company, title, None)
    earlier = datetime(2020, 1, 1, tzinfo=UTC)
    later = datetime(2021, 1, 1, tzinfo=UTC)
    try:
        older = JobPosting(
            source="board_api",
            source_name="greenhouse",
            source_type="board_api",
            title=title,
            description_text=f"desc a {suffix}",
            description_hash=f"hash-a|{suffix}|{make_uuid()}",
            dedupe_key=key,
            source_import_key=f"greenhouse|anduril|old|{suffix}"[:255],
        )
        newer = JobPosting(
            source="board_api",
            source_name="greenhouse",
            source_type="board_api",
            title=title,
            description_text=f"desc b {suffix}",
            description_hash=f"hash-b|{suffix}|{make_uuid()}",
            dedupe_key=key,
            source_import_key=f"greenhouse|anduril|new|{suffix}"[:255],
        )
        session.add_all([older, newer])
        session.flush()
        older.created_at = earlier
        newer.created_at = later
        session.commit()
        session.refresh(older)
        session.refresh(newer)

        result = find_duplicate_job(
            session,
            {
                "title": title,
                "company": company,
                "location": None,
                "description_text": f"totally unique body {suffix}",
                "source_name": "greenhouse",
            },
        )
        assert result.is_duplicate
        assert result.existing_id == older.id
        assert any(w.startswith("duplicate_match:") for w in result.warnings)
    finally:
        session.close()


def test_upsert_passes_duplicate_match_warnings() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    suffix = make_uuid()[:8]
    company = f"Anduril-{suffix}"
    title = f"Engineer-{suffix}"
    key = make_dedupe_key(company, title, None)
    earlier = datetime(2020, 1, 1, tzinfo=UTC)
    later = earlier + timedelta(days=1)
    try:
        first = JobPosting(
            source="board_api",
            source_name="greenhouse",
            source_type="board_api",
            title=title,
            description_text=f"desc a {suffix}",
            description_hash=f"hash-a|{suffix}|{make_uuid()}",
            dedupe_key=key,
            source_import_key=f"greenhouse|anduril|a|{suffix}"[:255],
        )
        second = JobPosting(
            source="board_api",
            source_name="greenhouse",
            source_type="board_api",
            title=title,
            description_text=f"desc b {suffix}",
            description_hash=f"hash-b|{suffix}|{make_uuid()}",
            dedupe_key=key,
            source_import_key=f"greenhouse|anduril|b|{suffix}"[:255],
        )
        session.add_all([first, second])
        session.flush()
        first.created_at = earlier
        second.created_at = later
        session.commit()

        warnings: list[str] = []
        job_id, created = upsert_job_from_import(
            session,
            {
                "title": title,
                "company": company,
                "description_text": f"unique body {suffix}",
                "location": None,
            },
            source_name="greenhouse",
            source_type="board_api",
            warnings_out=warnings,
        )
        assert job_id
        assert created is False
        assert any(w.startswith("duplicate_match:") for w in warnings)
        session.commit()
    finally:
        session.close()
