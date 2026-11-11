from __future__ import annotations

from fastapi.testclient import TestClient

from apps.dashboard.app import create_app


def test_cover_letter_and_resume_buttons_are_visible_on_job_detail() -> None:
    client = TestClient(create_app())
    feed = client.get("/")
    detail = client.get("/jobs/job-123")
    assert feed.status_code == 200
    assert "Generate tailored cover letter" not in feed.text
    assert "Generate tailored resume" not in feed.text
    assert "Open details" in feed.text or "Fit view" in feed.text
    assert detail.status_code == 200
    assert 'id="btn-generate-resume"' in detail.text
    assert 'id="btn-generate-cover"' in detail.text
    assert "Generate tailored resume" in detail.text
    assert "Generate tailored cover letter" in detail.text
    # Must not depend on Actions overflow menu for the primary controls.
    assert "actions-toggle" not in detail.text
    assert "cover-voice" in detail.text
