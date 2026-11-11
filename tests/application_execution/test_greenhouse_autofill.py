from __future__ import annotations

from pathlib import Path

import pytest

from apps.application_execution.adapters.greenhouse import (
    fill_greenhouse_form,
    is_greenhouse_url,
)
from apps.application_execution.services.approved_profile import ApprovedProfile
from apps.application_execution.services.submit_guard import (
    SubmitGuardError,
    assert_not_submit_action,
    looks_like_submit_action,
)

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "greenhouse_apply.html"
)


def test_submit_guard_blocks_submit_labels() -> None:
    assert looks_like_submit_action("Submit Application")
    assert looks_like_submit_action("Apply Now")
    assert not looks_like_submit_action("Save for later")
    with pytest.raises(SubmitGuardError):
        assert_not_submit_action("Submit")


def test_greenhouse_url_detection() -> None:
    assert is_greenhouse_url(
        "https://job-boards.greenhouse.io/accuweather/jobs/8084448"
    )
    assert not is_greenhouse_url("https://jobs.lever.co/example/abc")


def test_fill_fixture_labels_upload_non_overwrite(tmp_path: Path) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    resume = tmp_path / "job_specific_resume.pdf"
    resume.write_bytes(b"%PDF-1.4 job-specific")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    (desktop / "generic.pdf").write_bytes(b"%PDF-1.4 desktop")

    profile = ApprovedProfile(
        full_name="Alex Michael Manjakunnel",
        first_name="Alex",
        last_name="Manjakunnel",
        email="alex@example.com",
        phone="814-555-0100",
        linkedin_url="https://linkedin.com/in/example",
        school="Pennsylvania State University",
        degree="B.S.",
        discipline="Computer Science",
        education_start="2021",
        education_end="2025",
    )

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(FIXTURE.as_uri())
        filled, unresolved, resume_ok, cover_ok = fill_greenhouse_form(
            page,
            profile=profile,
            resume_path=resume,
            cover_letter_path=None,
            include_cover_letter=False,
        )
        by_key = {f.field_key: f for f in filled}
        assert by_key["first_name"].status == "filled"
        assert page.input_value("#first_name") == "Alex"
        assert by_key["email"].status == "skipped_existing"
        assert page.input_value("#email") == "preexisting@example.com"
        assert resume_ok is True
        assert cover_ok is False
        assert any(u.get("kind") == "sensitive_question" for u in unresolved)
        assert any(u.get("kind") == "final_submission" for u in unresolved)

        # Desktop generic must be refused by caller path; adapter also checks parent name.
        filled2, _, resume2, _ = fill_greenhouse_form(
            page,
            profile=profile,
            resume_path=desktop / "generic.pdf",
            cover_letter_path=None,
            include_cover_letter=False,
        )
        # File already attached from prior fill → skipped_existing, not desktop replace.
        assert any(
            f.field_key == "resume"
            and f.status in {"skipped_existing", "unsupported"}
            for f in filled2
        )
        assert resume2 is False

        with pytest.raises(SubmitGuardError):
            assert_not_submit_action(page.inner_text("#submit-app"))
        browser.close()
