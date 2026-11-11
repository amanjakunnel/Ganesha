from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from packages.core.services.job_refresh_service import (
    reset_refresh_state_for_tests,
    start_refresh,
)
from packages.core.services.simplifyjobs_sync import (
    SimplifyJobsSyncResult,
    mark_missing_simplifyjobs_closed,
)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_refresh_state_for_tests()
    yield
    reset_refresh_state_for_tests()


def test_concurrent_refresh_guard() -> None:
    with patch(
        "packages.core.services.job_refresh_service.threading.Thread"
    ) as thread_cls:
        thread_cls.return_value = MagicMock()
        state1, code1 = start_refresh()
        assert code1 == 202
        assert state1.status == "running"
        state2, code2 = start_refresh()
        assert code2 == 409
        assert state2.refresh_id == state1.refresh_id


def test_rate_limit_after_complete() -> None:
    with patch(
        "packages.core.services.job_refresh_service.threading.Thread"
    ) as thread_cls:
        thread_cls.return_value = MagicMock()
        state1, code1 = start_refresh()
        assert code1 == 202
        # Simulate completion + rate-limit window
        from packages.core.services import job_refresh_service as mod

        with mod._STATE_LOCK:
            mod._LAST_COMPLETED_AT = datetime.now(UTC)
            mod._ACTIVE = None
        rejected, code = start_refresh()
        assert code == 429
        assert rejected.status == "rejected"


def test_mark_missing_closed_counts() -> None:
    from packages.core.db import SessionLocal, get_engine
    from packages.core.domain.models import Base, JobPosting

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        kept = JobPosting(
            source="github_sync",
            source_name="simplifyjobs",
            source_type="github_sync",
            title="Kept Role",
            description_text="desc",
            description_hash="h1-refresh-test",
            dedupe_key="d1-refresh-test",
            source_import_key="keep-key-refresh-test",
            intake_metadata={"simplify_markers": {}},
        )
        gone = JobPosting(
            source="github_sync",
            source_name="simplifyjobs",
            source_type="github_sync",
            title="Gone Role",
            description_text="desc",
            description_hash="h2-refresh-test",
            dedupe_key="d2-refresh-test",
            source_import_key="gone-key-refresh-test",
            intake_metadata={"simplify_markers": {}},
        )
        session.add_all([kept, gone])
        session.commit()

        closed = mark_missing_simplifyjobs_closed(
            session, seen_import_keys={"keep-key-refresh-test"}
        )
        session.commit()
        assert closed == 1
        session.refresh(gone)
        assert gone.intake_metadata["simplify_markers"]["listing_closed"] is True
        session.refresh(kept)
        assert not (kept.intake_metadata.get("simplify_markers") or {}).get(
            "listing_closed"
        )
    finally:
        session.close()
        # Leave shared DB intact; unique keys avoid collisions with production data.


def test_per_source_failure_surface() -> None:
    """Failed SimplifyJobs fetch is recorded without crashing the refresh worker."""
    from packages.core.services.job_refresh_service import _run_refresh
    from packages.core.services.simplifyjobs_sync import SimplifyJobsFetchError

    with patch(
        "packages.core.services.job_refresh_service.threading.Thread"
    ) as thread_cls:
        thread_cls.return_value = MagicMock()
        state, code = start_refresh()
        assert code == 202

    with (
        patch(
            "packages.core.services.job_refresh_service.sync_simplifyjobs",
            side_effect=SimplifyJobsFetchError("network down"),
        ),
        patch(
            "packages.core.services.job_refresh_service.SessionLocal"
        ) as session_local,
    ):
        mock_session = MagicMock()
        session_local.return_value = mock_session
        _run_refresh(state.refresh_id)

    from packages.core.services import job_refresh_service as mod

    assert mod._HISTORY
    last = mod._HISTORY[-1]
    sj = next(s for s in last.sources if s.source_id == "simplifyjobs")
    assert sj.status == "failed"
    assert "network down" in (sj.error_summary or "")


def test_sync_result_tracks_new_updated_closed() -> None:
    result = SimplifyJobsSyncResult(
        parsed=10, created=2, updated=5, closed=3, fetched=True
    )
    assert result.created == 2
    assert result.updated == 5
    assert result.closed == 3
