from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import (
    Base,
    Company,
    JobApplication,
    JobPosting,
    make_dedupe_key,
    make_description_hash,
    make_uuid,
)
from packages.core.services.job_eligibility import (
    ELIGIBILITY_EXCLUDED,
    ELIGIBILITY_POLICY_VERSION,
    reassess_eligibility,
)


def _session() -> Session:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def test_reassess_excludes_internship_and_preserves_application() -> None:
    session = _session()
    try:
        company = Company(canonical_name="ExCo", normalized_key="exco")
        session.add(company)
        session.flush()
        job = JobPosting(
            id=make_uuid(),
            source="test",
            title="Software Engineering Intern",
            company_id=company.id,
            location="New York, NY",
            description_text=(
                "Build product features with mentors. Additional context to satisfy "
                "minimum description length requirements for eligibility checks."
            ),
            description_hash=make_description_hash("intern"),
            dedupe_key=make_dedupe_key("ExCo", "intern", "NY"),
            status="new",
            discovered_at=datetime.now(UTC),
            company_apply_url="https://example.com/a",
            source_import_key=f"reassess|{make_uuid()}",
            intake_metadata={
                "eligibility": {
                    "result": "actionable",
                    "reason_category": None,
                    "policy_version": "eligibility-v1",
                    "classified_at": datetime.now(UTC).isoformat(),
                }
            },
        )
        session.add(job)
        session.flush()
        app = JobApplication(
            id=make_uuid(),
            job_posting_id=job.id,
            status="draft",
            notes="keep me",
        )
        session.add(app)
        session.commit()

        preview = reassess_eligibility(session, dry_run=True)
        assert preview["dry_run"] is True
        assert preview["reason_counts"].get("internship_role", 0) >= 1
        session.refresh(job)
        assert (job.intake_metadata or {}).get("eligibility", {}).get("result") == "actionable"

        applied = reassess_eligibility(session, dry_run=False)
        session.commit()
        assert applied["policy_version"] == ELIGIBILITY_POLICY_VERSION
        session.refresh(job)
        assert job.intake_metadata["eligibility"]["result"] == ELIGIBILITY_EXCLUDED
        session.refresh(app)
        assert app.notes == "keep me"
        assert session.query(JobApplication).count() == 1
    finally:
        session.close()
