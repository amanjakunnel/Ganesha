"""Not-relevant overrides persist under ~/.ganesha path (tmp in tests)."""

from __future__ import annotations

from pathlib import Path

from packages.core.domain.models import JobPosting, make_uuid
from packages.core.services.role_profile import (
    STATUS_MISMATCH,
    RoleFamily,
    RoleProfile,
    classify_job_against_profile,
)
from packages.core.services.role_relevance_overrides import (
    REASON_USER_NOT_RELEVANT,
    RelevanceOverride,
    append_override,
    load_relevance_overrides,
    mark_job_not_relevant,
    override_matches_job,
)


def _job(title: str) -> JobPosting:
    return JobPosting(
        id=make_uuid(),
        source="manual",
        title=title,
        description_text="Build Python APIs on AWS with Kubernetes.",
        description_hash="x",
        dedupe_key=make_uuid(),
        status="new",
        location="Remote",
        company_apply_url="https://example.com/apply",
        intake_metadata={},
    )


class _Session:
    def add(self, _obj: object) -> None:
        return None

    def flush(self) -> None:
        return None


def test_mark_not_relevant_appends_override_and_never_deletes(tmp_path: Path) -> None:
    path = tmp_path / "role_relevance_overrides.json"
    job = _job("Sales Engineer (enterprise)")
    result = mark_job_not_relevant(_Session(), job, path=path)  # type: ignore[arg-type]
    assert result["deleted"] is False
    assert result["status"] == STATUS_MISMATCH
    assert (job.intake_metadata or {}).get("role_profile", {}).get("status") == STATUS_MISMATCH

    loaded = load_relevance_overrides(path)
    assert any(o.kind == "job_id" and o.value == job.id for o in loaded.overrides)

    profile = RoleProfile(
        families=[
            RoleFamily(
                id="software_engineering",
                label="SWE",
                title_patterns=[r"\bsoftware engineer\b"],
                responsibility_keywords=["python", "aws", "kubernetes"],
                enabled=True,
            )
        ]
    )
    assert override_matches_job(job, loaded) is not None
    classified = classify_job_against_profile(
        job, profile, relevance_overrides=loaded
    )
    assert classified["status"] == STATUS_MISMATCH
    assert classified.get("user_override") is True
    assert classified.get("reason") == REASON_USER_NOT_RELEVANT


def test_title_pattern_override(tmp_path: Path) -> None:
    path = tmp_path / "role_relevance_overrides.json"
    append_override(
        RelevanceOverride(kind="title_pattern", value=r"^Kitchen Operations"),
        path=path,
    )
    loaded = load_relevance_overrides(path)
    job = _job("Kitchen Operations Associate, DashMart")
    hit = override_matches_job(job, loaded)
    assert hit is not None
    assert hit.kind == "title_pattern"
