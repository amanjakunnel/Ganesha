from __future__ import annotations

from fastapi.testclient import TestClient

from apps.dashboard.app import create_app


def test_cover_letter_actions_are_only_on_job_detail() -> None:
    client = TestClient(create_app())
    feed = client.get("/")
    detail = client.get("/jobs/job-123")
    assert feed.status_code == 200
    assert "Generate tailored cover letter" not in feed.text
    assert "View cover-letter plan" not in feed.text
    assert detail.status_code == 200
    assert "Generate tailored resume" in detail.text
    assert "Generate tailored cover letter" in detail.text
    assert "View cover-letter plan" in detail.text
    assert "cover-voice" in detail.text
    assert "technical_direct" in detail.text
