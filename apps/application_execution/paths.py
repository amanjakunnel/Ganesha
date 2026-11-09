"""Local paths for application-attempt packages (not cloud artifact storage)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ATTEMPTS_ROOT = REPO_ROOT / "data" / "application_attempts"

EXECUTION_MODE_CHROME_SIMPLIFY = "chrome_simplify_handoff"
EXECUTION_MODE_CONTROLLED = "controlled_browser"

ATTEMPT_STATUSES = frozenset(
    {
        "prepared",
        "opened",
        "needs_input",
        "ready_for_review",
        "submitted_user_confirmed",
        "failed",
        "cancelled",
    }
)

ACTIVE_ATTEMPT_STATUSES = frozenset(
    {"prepared", "opened", "needs_input", "ready_for_review"}
)

# Categories that must never be auto-filled from eligibility or inferred profile.
SENSITIVE_ANSWER_CATEGORIES = frozenset(
    {
        "work_authorization",
        "sponsorship",
        "citizenship",
        "security_clearance",
        "salary_expectation",
        "relocation",
        "availability_start_date",
        "demographic_disclosure",
        "disability_disclosure",
        "legal_declaration",
        "consent",
    }
)


def attempt_dir(job_id: str, attempt_id: str) -> Path:
    return ATTEMPTS_ROOT / job_id / attempt_id
