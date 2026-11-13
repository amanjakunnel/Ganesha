"""Score-remaining loop and refresh auto-score for new jobs only."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from packages.core.domain.models import JobPosting, make_uuid
from packages.core.services.job_refresh_service import (
    RefreshJobState,
    _classify_and_score_new_jobs,
    reset_refresh_state_for_tests,
)
from packages.core.services.job_score_service import (
    reset_score_state_for_tests,
    score_remaining,
)


def test_score_remaining_stops_when_batch_empty() -> None:
    session = MagicMock()
    calls = {"n": 0}

    def fake_batch(_session, *, limit, role_filtered_only):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "assessed": 2,
                "bands": {"recommended": 1, "stretch": 1},
                "skipped_scored": 0,
                "skipped_mismatch": 0,
            }
        return {
            "assessed": 0,
            "bands": {},
            "skipped_scored": 5,
            "skipped_mismatch": 1,
        }

    with (
        patch(
            "packages.core.services.job_score_service.assess_fit_batch",
            side_effect=fake_batch,
        ),
        patch(
            "packages.core.services.job_score_service.feed_counts_snapshot",
            return_value={"counts": {"unscored_fit": 0}},
        ),
    ):
        result = score_remaining(session, batch_size=200, max_rounds=10)

    assert result["assessed"] == 2
    assert result["rounds"] == 2
    assert result["bands"]["recommended"] == 1
    assert result["stopped_reason"] == "complete"
    assert session.commit.call_count == 2


def test_score_remaining_idempotent_second_pass() -> None:
    session = MagicMock()

    with (
        patch(
            "packages.core.services.job_score_service.assess_fit_batch",
            return_value={
                "assessed": 0,
                "bands": {},
                "skipped_scored": 10,
                "skipped_mismatch": 0,
            },
        ) as batch,
        patch(
            "packages.core.services.job_score_service.feed_counts_snapshot",
            return_value={"counts": {"unscored_fit": 0}},
        ),
    ):
        first = score_remaining(session, batch_size=50, max_rounds=5)
        second = score_remaining(session, batch_size=50, max_rounds=5)

    assert first["assessed"] == 0
    assert second["assessed"] == 0
    assert batch.call_count == 2


def test_refresh_scores_only_new_unscored_matched() -> None:
    reset_refresh_state_for_tests()
    reset_score_state_for_tests()

    matched = JobPosting(
        id=make_uuid(),
        source="board",
        source_name="greenhouse",
        title="Matched Unscored",
        description_text="desc " * 40,
        description_hash=make_uuid(),
        dedupe_key=make_uuid(),
        status="new",
        intake_metadata={
            "eligibility": {"result": "actionable"},
            "role_profile": {"status": "matched"},
        },
    )
    mismatch = JobPosting(
        id=make_uuid(),
        source="board",
        source_name="greenhouse",
        title="Mismatch",
        description_text="desc " * 40,
        description_hash=make_uuid(),
        dedupe_key=make_uuid(),
        status="new",
        intake_metadata={
            "eligibility": {"result": "actionable"},
            "role_profile": {"status": "role_family_mismatch"},
        },
    )
    already = JobPosting(
        id=make_uuid(),
        source="board",
        source_name="greenhouse",
        title="Already scored",
        description_text="desc " * 40,
        description_hash=make_uuid(),
        dedupe_key=make_uuid(),
        status="new",
        intake_metadata={
            "eligibility": {"result": "actionable"},
            "role_profile": {"status": "matched"},
            "evidence_fit": {"band": "recommended", "score": 90},
        },
    )
    by_id = {matched.id: matched, mismatch.id: mismatch, already.id: already}
    ids = [matched.id, mismatch.id, already.id]
    idx = {"i": 0}
    scored_ids: list[str] = []

    def fake_assess(_session, job):
        scored_ids.append(job.id)
        return MagicMock(band="recommended", score=70)

    session = MagicMock()

    def filter_fn(*_a, **_k):
        mq = MagicMock()
        jid = ids[idx["i"]]
        idx["i"] += 1
        mq.one_or_none.return_value = by_id[jid]
        return mq

    session.query.return_value.filter.side_effect = filter_fn
    state = RefreshJobState(refresh_id="t", status="running")

    with (
        patch(
            "packages.core.services.role_profile.load_role_profile",
            return_value=MagicMock(),
        ),
        patch(
            "packages.core.services.role_profile.get_stored_role_block",
            side_effect=lambda job: (job.intake_metadata or {}).get("role_profile"),
        ),
        patch(
            "packages.core.services.role_profile.classify_and_store",
            return_value={"status": "matched"},
        ),
        patch(
            "packages.core.services.evidence_fit_service.get_stored_fit",
            side_effect=lambda job: (job.intake_metadata or {}).get("evidence_fit"),
        ),
        patch(
            "packages.core.services.evidence_fit_service.assess_and_store",
            side_effect=fake_assess,
        ),
    ):
        n = _classify_and_score_new_jobs(
            session,
            ids,
            state=state,
            score_limit=500,
        )

    assert n == 1
    assert scored_ids == [matched.id]
    assert state.scoring_progress is not None
    assert "Scoring jobs" in (state.progress or "")


def test_loading_js_defines_progress_status() -> None:
    """JS-free smoke: ProgressStatus helper source is present and complete."""
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "dashboard"
        / "static"
        / "loading.js"
    )
    text = path.read_text(encoding="utf-8")
    assert "class ProgressStatus" in text
    assert "prefers-reduced-motion" in text
    assert "aria-live" in text
    assert "setDone" in text
    assert "setError" in text
    assert "window.ProgressStatus" in text
