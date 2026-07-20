from typing import Any
from typer.testing import CliRunner

from apps.cli.main import app
import packages.core.services.job_service as js

from packages.core.domain.models import Base
from packages.core.db import get_engine

runner = CliRunner()


def setup_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


def teardown_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)


def test_referral_start_unknown_job_shows_friendly_error(monkeypatch: Any) -> None:
    # Make start_referral raise JobNotFoundError
    def _raise(job_session: Any, job_id: str, cutoff_hours: int = 48) -> None:
        raise js.JobNotFoundError(job_id)

    monkeypatch.setattr(js, "start_referral", _raise)
    result = runner.invoke(app, ["jobs", "referral-start", "nope-id"])
    assert result.exit_code == 1
    assert "Job not found: nope-id" in result.stdout
