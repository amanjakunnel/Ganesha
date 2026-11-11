from __future__ import annotations

from unittest.mock import MagicMock, patch

from packages.core.domain.models import JobPosting, make_description_hash, make_dedupe_key, make_uuid
from packages.core.services.job_liveness import check_job_liveness


def test_liveness_marks_dead_board() -> None:
    job = JobPosting(
        id=make_uuid(),
        source="test",
        title="Role",
        description_text="x" * 40,
        description_hash=make_description_hash("x" * 40),
        dedupe_key=make_dedupe_key("Co", "Role", None),
        company_apply_url="https://boards.greenhouse.io/example/jobs/1",
        intake_metadata={},
    )
    session = MagicMock()
    response = MagicMock()
    response.status_code = 404
    response.text = "Job not found"
    with patch("packages.core.services.job_liveness.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = response
        result = check_job_liveness(session, job, mark_closed=True)
    assert result.live is False
    assert result.marked_closed is True
    assert job.intake_metadata["simplify_markers"]["listing_closed"] is True
