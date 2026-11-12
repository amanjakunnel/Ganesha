from __future__ import annotations

from packages.core.services.job_eligibility import (
    ELIGIBILITY_ACTIONABLE,
    ELIGIBILITY_EXCLUDED,
    ELIGIBILITY_NEEDS_REVIEW,
    classify_job_eligibility,
)


def _long(text: str) -> str:
    return (
        text
        + " Additional responsibilities and team context to satisfy minimum "
        "description length for eligibility classification checks."
    )


def test_us_citizenship_required_excluded() -> None:
    result = classify_job_eligibility(
        title="Software Engineer",
        description=_long("Candidates must be a U.S. citizen. Build backend services."),
        company_apply_url="https://example.com/a",
        location="Arlington, VA",
        description_verified=True,
    )
    assert result.result == ELIGIBILITY_EXCLUDED
    assert result.reason_category == "citizenship_requirement"


def test_eeo_boilerplate_citizenship_not_excluded() -> None:
    result = classify_job_eligibility(
        title="Software Engineer",
        description=_long(
            "Acme is an equal employment opportunity employer. All qualified applicants "
            "will receive consideration without regard to race, citizenship, or disability "
            "status. Build distributed systems with the platform team."
        ),
        company_apply_url="https://example.com/a",
        location="New York, NY",
        description_verified=True,
    )
    assert result.result == ELIGIBILITY_ACTIONABLE


def test_does_not_require_citizenship_negation() -> None:
    result = classify_job_eligibility(
        title="Engineer",
        description=_long(
            "This role does not require U.S. citizenship. Remote-friendly US team."
        ),
        company_apply_url="https://example.com/a",
        location="Remote",
        description_verified=True,
    )
    assert result.result == ELIGIBILITY_ACTIONABLE


def test_clearance_required_excluded_preferred_ok() -> None:
    hard = classify_job_eligibility(
        title="Systems Engineer",
        description=_long("Active Top Secret clearance required for this role."),
        company_apply_url="https://example.com/a",
        location="McLean, VA",
        description_verified=True,
    )
    assert hard.result == ELIGIBILITY_EXCLUDED
    assert hard.reason_category == "clearance_requirement"

    preferred = classify_job_eligibility(
        title="Systems Engineer",
        description=_long(
            "Security clearance preferred but not required. Ship product features."
        ),
        company_apply_url="https://example.com/a",
        location="McLean, VA",
        description_verified=True,
    )
    assert preferred.result == ELIGIBILITY_ACTIONABLE


def test_no_sponsorship_hard_excluded() -> None:
    result = classify_job_eligibility(
        title="Engineer",
        description=_long(
            "Must be authorized to work in the United States without sponsorship now or "
            "in the future. We will not sponsor visas for this role."
        ),
        company_apply_url="https://example.com/a",
        location="Austin, TX",
        description_verified=True,
    )
    assert result.result == ELIGIBILITY_EXCLUDED
    assert result.reason_category == "sponsorship_unavailable"


def test_opt_ambiguous_included_with_flag() -> None:
    result = classify_job_eligibility(
        title="Engineer",
        description=_long(
            "STEM OPT and CPT candidates are welcome. H-1B sponsorship is evaluated "
            "case-by-case and may be unavailable for some teams."
        ),
        company_apply_url="https://example.com/a",
        location="Seattle, WA",
        description_verified=True,
    )
    assert result.result == ELIGIBILITY_ACTIONABLE
    assert "sponsorship_ambiguous" in result.flags
    assert result.evidence_excerpt


def test_tag_vs_text_conflict_full_text_wins() -> None:
    # Tag says no sponsorship; text offers sponsorship → include with conflict flag.
    result = classify_job_eligibility(
        title="Engineer",
        description=_long(
            "Visa sponsorship is available for exceptional candidates joining this team."
        ),
        company_apply_url="https://example.com/a",
        location="Boston, MA",
        source_markers={"no_sponsorship": True, "citizenship_required": False},
        description_verified=True,
    )
    assert result.result == ELIGIBILITY_ACTIONABLE
    assert "sponsorship_conflict" in result.flags or "source_text_conflict" in result.flags

    # Tag citizenship without confirming text → flag, not exclude.
    tagged = classify_job_eligibility(
        title="Engineer",
        description=_long("Build APIs and data pipelines for enterprise customers."),
        company_apply_url="https://example.com/a",
        location="Chicago, IL",
        source_markers={"citizenship_required": True},
        description_verified=True,
    )
    assert tagged.result == ELIGIBILITY_ACTIONABLE
    assert "source_tag_citizenship" in tagged.flags


def test_unverified_text_sponsorship_unknown_not_excluded() -> None:
    stub = (
        "Company: Acme\nRole: Software Engineer New Grad\nLocation: NY\n"
        "Source: https://github.com/SimplifyJobs/New-Grad-Positions"
    )
    result = classify_job_eligibility(
        title="Software Engineer New Grad",
        description=stub,
        company_apply_url="https://example.com/a",
        location="New York, NY",
        source_markers={"no_sponsorship": True, "citizenship_required": True},
        description_verified=False,
    )
    assert result.result == ELIGIBILITY_NEEDS_REVIEW
    assert result.reason_category == "description_unverified"
    assert "sponsorship_unknown" in result.flags
    assert result.result != ELIGIBILITY_EXCLUDED


def test_itar_us_person_excluded() -> None:
    result = classify_job_eligibility(
        title="Embedded Engineer",
        description=_long(
            "This position requires US person status under ITAR regulations."
        ),
        company_apply_url="https://example.com/a",
        location="Huntsville, AL",
        description_verified=True,
    )
    assert result.result == ELIGIBILITY_EXCLUDED
    assert result.reason_category == "citizenship_requirement"
