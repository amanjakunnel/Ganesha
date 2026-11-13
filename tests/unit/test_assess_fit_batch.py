"""Unit tests for assess_fit_batch / drain and refresh auto-score progress."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from packages.core.domain.models import JobPosting, make_uuid
from packages.core.services.job_refresh_service import _assess_new_jobs
from packages.core.services.role_profile import (
    STATUS_MATCHED,
    STATUS_MISMATCH,
    assess_fit_batch,
    assess_fit_drain,
)


def _job(
    title: str,
    *,
    fit: dict | None = None,
    role_status: str = STATUS_MATCHED,
    eligibility: str = "actionable",
) -> JobPosting:
    meta: dict = {
        "eligibility": {"result": eligibility},
        "role_profile": {
            "status": role_status,
            "matched_family": "backend" if role_status == STATUS_MATCHED else None,
            "reason": "test",
        },
    }
    if fit is not None:
        meta["evidence_fit"] = fit
    return JobPosting(
        id=make_uuid(),
        source="manual",
        title=title,
        description_text="Build Python APIs on AWS with Docker. " * 30,
        description_hash=make_uuid(),
        dedupe_key=make_uuid(),
        status="new",
        location="Remote, US",
        company_apply_url="https://boards.greenhouse.io/acme/jobs/1",
        intake_metadata=meta,
    )


class _Query:
    def __init__(self, rows: list[JobPosting]) -> None:
        self._rows = list(rows)
        self._offset = 0
        self._limit: int | None = None

    def order_by(self, *_a: object, **_k: object) -> _Query:
        return self

    def offset(self, n: int) -> _Query:
        self._offset = n
        return self

    def limit(self, n: int) -> _Query:
        self._limit = n
        return self

    def filter(self, *_a: object, **_k: object) -> _Query:
        return self

    def all(self) -> list[JobPosting]:
        rows = self._rows[self._offset :]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def one_or_none(self) -> JobPosting | None:
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, jobs: list[JobPosting]) -> None:
        self.jobs = jobs
        self.added: list[object] = []

    def query(self, *_a: object, **_k: object) -> _Query:
        return _Query(self.jobs)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        return None


def test_assess_fit_batch_scores_unscored_skips_scored_and_mismatch() -> None:
    scored = _job("Already Scored", fit={"band": "recommended", "score": 80})
    mismatch = _job("Sales Lead", role_status=STATUS_MISMATCH)
    target = _job("Backend Engineer")
    session = _Session([scored, mismatch, target])
    assessment = SimpleNamespace(band="recommended", score=77)

    with (
        patch(
            "packages.core.services.role_profile.load_role_profile",
            return_value=MagicMock(),
        ),
        patch(
            "apps.resume_generation.services.resume_fact_service.usable_facts",
            return_value=[],
        ),
        patch(
            "packages.core.services.role_profile._evidence_blob",
            return_value="",
        ),
        patch(
            "packages.core.services.evidence_fit_service.assess_and_store",
            return_value=assessment,
        ) as assess,
        patch(
            "packages.core.services.job_eligibility.resolve_job_eligibility",
            side_effect=lambda job: SimpleNamespace(
                result=(job.intake_metadata or {}).get("eligibility", {}).get("result")
            ),
        ),
    ):
        result = assess_fit_batch(session, limit=10, role_filtered_only=True)

    assert result["assessed"] == 1
    assert result["skipped_scored"] == 1
    assert result["skipped_mismatch"] == 1
    assert result["bands"]["recommended"] == 1
    assert assess.call_count == 1


def test_assess_fit_batch_skips_excluded() -> None:
    excluded = _job("Intern", eligibility="excluded")
    session = _Session([excluded])
    with (
        patch(
            "packages.core.services.role_profile.load_role_profile",
            return_value=MagicMock(),
        ),
        patch(
            "apps.resume_generation.services.resume_fact_service.usable_facts",
            return_value=[],
        ),
        patch(
            "packages.core.services.role_profile._evidence_blob",
            return_value="",
        ),
        patch(
            "packages.core.services.evidence_fit_service.assess_and_store"
        ) as assess,
        patch(
            "packages.core.services.job_eligibility.resolve_job_eligibility",
            return_value=SimpleNamespace(result="excluded"),
        ),
    ):
        result = assess_fit_batch(session, limit=5, eligible_only=True)

    assert result["assessed"] == 0
    assert result["skipped_excluded"] == 1
    assess.assert_not_called()


def test_assess_fit_drain_loops_until_empty() -> None:
    calls = {"n": 0}

    def _fake_batch(session: object, **kwargs: object) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "assessed": 2,
                "skipped_scored": 0,
                "skipped_mismatch": 0,
                "skipped_excluded": 0,
                "bands": {"recommended": 2},
            }
        return {
            "assessed": 0,
            "skipped_scored": 3,
            "skipped_mismatch": 1,
            "skipped_excluded": 0,
            "bands": {},
        }

    with patch(
        "packages.core.services.role_profile.assess_fit_batch",
        side_effect=_fake_batch,
    ):
        result = assess_fit_drain(_Session([]), batch_size=50)

    assert result["assessed"] == 2
    assert result["batches"] == 2
    assert result["exhausted"] is True
    assert result["bands"]["recommended"] == 2


def test_assess_new_jobs_reports_progress_and_skips_excluded() -> None:
    good = _job("Good Role")
    excluded = _job("Bad Role", eligibility="excluded")
    by_id = {good.id: good, excluded.id: excluded}

    class _Filt:
        def __init__(self, job: JobPosting | None) -> None:
            self._job = job

        def one_or_none(self) -> JobPosting | None:
            return self._job

    class _Sess:
        def query(self, *_a: object, **_k: object) -> _Sess:
            return self

        def filter(self, *args: object, **_k: object) -> _Filt:
            # SQLAlchemy BinaryExpression; fall back to sequential ids
            for jid, job in by_id.items():
                # Match whatever filter was applied by returning next unused
                if getattr(self, "_seen", None) is None:
                    self._seen = set()  # type: ignore[attr-defined]
                if jid not in self._seen:  # type: ignore[attr-defined]
                    self._seen.add(jid)  # type: ignore[attr-defined]
                    return _Filt(job)
            return _Filt(None)

    progress: list[tuple[int, int]] = []

    with (
        patch(
            "packages.core.services.job_eligibility.resolve_job_eligibility",
            side_effect=lambda job: SimpleNamespace(
                result=(job.intake_metadata or {}).get("eligibility", {}).get("result")
            ),
        ),
        patch(
            "packages.core.services.evidence_fit_service.get_stored_fit",
            return_value=None,
        ),
        patch(
            "packages.core.services.evidence_fit_service.assess_and_store",
            return_value=SimpleNamespace(band="stretch", score=55),
        ) as assess,
    ):
        n = _assess_new_jobs(
            _Sess(),  # type: ignore[arg-type]
            [good.id, excluded.id],
            progress_cb=lambda done, total: progress.append((done, total)),
        )

    assert n == 1
    assert assess.call_count == 1
    assert progress[0] == (0, 1)
    assert progress[-1] == (1, 1)
