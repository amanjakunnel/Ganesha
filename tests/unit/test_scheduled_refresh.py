"""Scheduler flag parsing and dry-run pipeline (no live sync)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import Base
from packages.core.services.scheduled_refresh import (
    parse_scheduled_refresh_args,
    run_scheduled_refresh,
)


def test_parse_scheduled_refresh_args() -> None:
    ns = parse_scheduled_refresh_args(
        ["--dry-run", "--skip-refresh", "--score-limit", "10", "--enrich-limit", "5"]
    )
    assert ns.dry_run is True
    assert ns.skip_refresh is True
    assert ns.skip_score is False
    assert ns.skip_enrich is False
    assert ns.score_limit == 10
    assert ns.enrich_limit == 5


def test_run_scheduled_refresh_dry_run_writes_log(tmp_path: Path) -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        with patch(
            "packages.core.services.role_profile.classify_jobs_batch",
            return_value={"scanned": 0, "applied": 0},
        ), patch(
            "packages.core.services.role_profile.assess_fit_batch",
            return_value={"assessed": 0},
        ):
            result = run_scheduled_refresh(
                session,
                dry_run=True,
                skip_refresh=True,
                score_limit=5,
                enrich_limit=3,
                log_dir=tmp_path,
            )
        assert result.dry_run is True
        assert result.opened_browser is False
        assert result.submitted_application is False
        assert result.finished_at
        logs = list(tmp_path.glob("scheduled-refresh-*.json"))
        assert len(logs) == 1
        assert (tmp_path / "scheduled-refresh.log").is_file()
    finally:
        session.close()
