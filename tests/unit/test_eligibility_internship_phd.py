from __future__ import annotations

from packages.core.services.job_eligibility import (
    ELIGIBILITY_ACTIONABLE,
    ELIGIBILITY_EXCLUDED,
    ELIGIBILITY_NEEDS_REVIEW,
    classify_job_eligibility,
)


def _long(text: str) -> str:
    return text + " Additional context to satisfy minimum description length requirements for eligibility."


def test_internship_title_excluded() -> None:
    result = classify_job_eligibility(
        title="Software Engineering Intern",
        description=_long("Build features with the platform team."),
        company_apply_url="https://example.com/apply",
        location="New York, NY",
    )
    assert result.result == ELIGIBILITY_EXCLUDED
    assert result.reason_category == "internship_role"


def test_internship_employment_type_excluded() -> None:
    result = classify_job_eligibility(
        title="Software Engineer",
        description=_long("Join our engineering organization."),
        company_apply_url="https://example.com/apply",
        location="Austin, TX",
        employment_type="Internship",
    )
    assert result.result == ELIGIBILITY_EXCLUDED
    assert result.reason_category == "internship_role"


def test_internal_tools_not_internship() -> None:
    result = classify_job_eligibility(
        title="Software Engineer, Internal Tools",
        description=_long(
            "Build internal developer platforms and tooling for product engineers."
        ),
        company_apply_url="https://example.com/apply",
        location="Seattle, WA",
    )
    assert result.result == ELIGIBILITY_ACTIONABLE


def test_full_time_mentioning_past_internship_not_excluded() -> None:
    result = classify_job_eligibility(
        title="Software Engineer",
        description=_long(
            "Full-time role. Prior internship experience is a plus but not required."
        ),
        company_apply_url="https://example.com/apply",
        location="Boston, MA",
    )
    assert result.result == ELIGIBILITY_ACTIONABLE


def test_phd_required_excluded() -> None:
    result = classify_job_eligibility(
        title="Research Engineer",
        description=_long("PhD required. Lead applied research projects."),
        company_apply_url="https://example.com/apply",
        location="Cambridge, MA",
    )
    assert result.result == ELIGIBILITY_EXCLUDED
    assert result.reason_category == "phd_required"


def test_must_hold_doctorate_excluded() -> None:
    result = classify_job_eligibility(
        title="Scientist",
        description=_long("Candidates must hold a doctorate in computer science."),
        company_apply_url="https://example.com/apply",
        location="San Francisco, CA",
    )
    assert result.result == ELIGIBILITY_EXCLUDED
    assert result.reason_category == "phd_required"


def test_phd_preferred_not_excluded() -> None:
    result = classify_job_eligibility(
        title="ML Engineer",
        description=_long("Master's required. PhD preferred."),
        company_apply_url="https://example.com/apply",
        location="New York, NY",
    )
    assert result.result == ELIGIBILITY_ACTIONABLE


def test_degree_alternatives_not_excluded() -> None:
    result = classify_job_eligibility(
        title="Engineer",
        description=_long(
            "Bachelor's, Master's, or PhD in Computer Science. Build distributed systems."
        ),
        company_apply_url="https://example.com/apply",
        location="Chicago, IL",
    )
    assert result.result == ELIGIBILITY_ACTIONABLE

    alt = classify_job_eligibility(
        title="Engineer",
        description=_long(
            "Master's with 2 years or PhD with 0 years. Shipping production services."
        ),
        company_apply_url="https://example.com/apply",
        location="Denver, CO",
    )
    assert alt.result == ELIGIBILITY_ACTIONABLE

    equiv = classify_job_eligibility(
        title="Engineer",
        description=_long("PhD or equivalent experience in machine learning systems."),
        company_apply_url="https://example.com/apply",
        location="Remote",
    )
    assert equiv.result == ELIGIBILITY_ACTIONABLE


def test_ambiguous_phd_needs_review() -> None:
    result = classify_job_eligibility(
        title="Researcher",
        description=_long(
            "Education: doctoral training required for this research track posting."
        ),
        company_apply_url="https://example.com/apply",
        location="Pittsburgh, PA",
    )
    assert result.result == ELIGIBILITY_NEEDS_REVIEW
    assert result.reason_category == "phd_requirement_unclear"


def test_phd_title_new_college_grad_excluded() -> None:
    result = classify_job_eligibility(
        title="Research Scientist, ML Systems - PhD New College Grad 2026",
        description=_long("Join our research org building ML systems infrastructure."),
        company_apply_url="https://example.com/apply",
        location="Santa Clara, CA",
        description_verified=True,
    )
    assert result.result == ELIGIBILITY_EXCLUDED
    assert result.reason_category == "phd_required"


def test_phd_students_title_excluded() -> None:
    result = classify_job_eligibility(
        title="Research Intern for PhD Students",
        description=_long("Lab collaboration on systems research projects."),
        company_apply_url="https://example.com/apply",
        location="Cambridge, MA",
        description_verified=True,
    )
    # Internship wins first when both apply.
    assert result.result == ELIGIBILITY_EXCLUDED
    assert result.reason_category in {"internship_role", "phd_required"}


def test_currently_pursuing_phd_excluded() -> None:
    result = classify_job_eligibility(
        title="Research Scientist",
        description=_long(
            "Candidates must be currently pursuing a PhD in computer science."
        ),
        company_apply_url="https://example.com/apply",
        location="Seattle, WA",
        description_verified=True,
    )
    assert result.result == ELIGIBILITY_EXCLUDED
    assert result.reason_category == "phd_required"


def test_aggregator_stub_internship_role_line_excluded() -> None:
    stub = (
        "Company: Acme\n"
        "Role: Software Engineering Intern - Summer 2026\n"
        "Location: New York, NY\n"
        "Category: Software Engineering\n"
        "Source: https://github.com/SimplifyJobs/New-Grad-Positions"
    )
    result = classify_job_eligibility(
        title="Software Engineering Intern - Summer 2026",
        description=stub,
        company_apply_url="https://example.com/apply",
        location="New York, NY",
        employment_type="full_time",
    )
    assert result.result == ELIGIBILITY_EXCLUDED
    assert result.reason_category == "internship_role"


def test_coop_title_excluded() -> None:
    result = classify_job_eligibility(
        title="Software Engineer Co-op",
        description=_long("Rotational co-op program with mentoring."),
        company_apply_url="https://example.com/apply",
        location="Toronto, ON, Canada",
    )
    assert result.result == ELIGIBILITY_EXCLUDED
    assert result.reason_category == "internship_role"
