"""Role profile derivation, Layer-1 classify, overrides, and other-roles filter."""

from __future__ import annotations

from pathlib import Path

from apps.dashboard.schemas import FitFeedFilter, JobQueryParams
from apps.dashboard.services.jobs_dashboard_service import (
    _matches_fit_filter,
    compute_fit_counts,
)
from packages.core.domain.models import JobPosting, ResumeFact, make_uuid
from packages.core.services.evidence_fit_service import BAND_RECOMMENDED
from packages.core.services.role_profile import (
    STATUS_MATCHED,
    STATUS_MISMATCH,
    RoleFamily,
    RoleProfile,
    UserOverrides,
    classify_job_against_profile,
    derive_role_profile,
    merge_profile,
    save_role_profile_overrides,
)


def _fact(text: str, *, skills: list[str] | None = None) -> ResumeFact:
    return ResumeFact(
        id=make_uuid(),
        source_document_id=make_uuid(),
        section="experience",
        source_text=text,
        normalized_text=text.lower(),
        content_hash=make_uuid(),
        usable=True,
        skills_json=skills or [],
    )


def _job(title: str, description: str = "", *, location: str = "Remote") -> JobPosting:
    return JobPosting(
        id=make_uuid(),
        source="manual",
        title=title,
        description_text=description,
        description_hash="x",
        dedupe_key=make_uuid(),
        status="new",
        location=location,
        company_apply_url="https://example.com/apply",
        intake_metadata={},
    )


class _FakeQuery:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def filter(self, *_a: object, **_k: object) -> _FakeQuery:
        return self

    def all(self) -> list[object]:
        return self._rows


class _FakeSession:
    """Minimal session stub for derive_role_profile track lookup."""

    def query(self, *_a: object, **_k: object) -> _FakeQuery:
        return _FakeQuery([])


def test_derive_role_profile_from_synthetic_facts() -> None:
    facts = [
        _fact(
            "Built Kubernetes platforms on AWS with Terraform and CI/CD.",
            skills=["kubernetes", "aws", "terraform"],
        ),
        _fact(
            "Owned backend REST APIs in Python and PostgreSQL.",
            skills=["python", "api", "postgresql"],
        ),
        _fact(
            "Tuned distributed Kafka pipelines for throughput.",
            skills=["kafka"],
        ),
    ]
    profile = derive_role_profile(_FakeSession(), facts=facts)  # type: ignore[arg-type]
    ids = {f.id for f in profile.families}
    assert "cloud_platform" in ids
    assert "backend" in ids
    assert profile.evidence_hash
    assert all(f.enabled for f in profile.families)
    assert all(f.evidence_fact_ids for f in profile.families)


def test_sales_title_is_role_family_mismatch() -> None:
    profile = RoleProfile(
        families=[
            RoleFamily(
                id="backend",
                label="Backend",
                title_patterns=[r"\bbackend\b"],
                responsibility_keywords=["api", "python", "backend"],
                enabled=True,
            )
        ]
    )
    job = _job(
        "Account Executive — Enterprise Sales",
        "Drive quota and pipeline generation with customers. No engineering work.",
    )
    result = classify_job_against_profile(job, profile)
    assert result["status"] == STATUS_MISMATCH
    reason = (result.get("reason") or "").lower()
    assert "sales" in reason or "non-software" in reason


def test_unfamiliar_title_with_matching_description_passes() -> None:
    profile = RoleProfile(
        families=[
            RoleFamily(
                id="cloud_platform",
                label="Cloud / Platform",
                title_patterns=[r"\bplatform engineer\b"],
                responsibility_keywords=[
                    "kubernetes",
                    "aws",
                    "terraform",
                    "infrastructure",
                    "docker",
                ],
                enabled=True,
            )
        ]
    )
    job = _job(
        "Member of Technical Staff, Warp Core",
        "Build Kubernetes infrastructure on AWS with Terraform and Docker. "
        "Own platform reliability and CI/CD for internal developer services.",
    )
    result = classify_job_against_profile(job, profile)
    assert result["status"] == STATUS_MATCHED
    assert result["matched_family"] == "cloud_platform"


def test_override_persistence(tmp_path: Path) -> None:
    from packages.core.services.role_profile import _save_raw

    path = tmp_path / "role_profile.json"
    derived = RoleProfile(
        evidence_hash="abc",
        families=[
            RoleFamily(
                id="backend",
                label="Backend",
                title_patterns=[r"\bbackend\b"],
                responsibility_keywords=["api"],
                enabled=True,
            ),
            RoleFamily(
                id="ml_systems",
                label="ML",
                title_patterns=[r"\bml\b"],
                responsibility_keywords=["pytorch"],
                enabled=True,
            ),
        ],
    )
    _save_raw(derived.to_dict(), path)

    overrides = UserOverrides(
        disabled_ids=["ml_systems"],
        removed_ids=[],
        added_families=[
            RoleFamily(
                id="data_eng",
                label="Data Engineering",
                title_patterns=[r"data engineer"],
                responsibility_keywords=["spark", "etl"],
                enabled=True,
                source="custom",
            )
        ],
    )
    merged = save_role_profile_overrides(overrides, path=path)
    assert merged.user_overrides.disabled_ids == ["ml_systems"]
    ids = {f.id: f for f in merged.families}
    assert "data_eng" in ids
    assert ids["ml_systems"].enabled is False
    assert ids["backend"].enabled is True

    again = merge_profile(derived, overrides)
    assert {f.id for f in again.families} == {"backend", "ml_systems", "data_eng"}


def test_layer1_true_positives_software_titles() -> None:
    """Regression: real SWE / platform titles still match."""
    profile = RoleProfile(
        families=[
            RoleFamily(
                id="cloud_platform",
                label="Cloud / Platform",
                title_patterns=[
                    r"\b(platform engineer|cloud engineer|infrastructure engineer)\b"
                ],
                responsibility_keywords=[
                    "kubernetes",
                    "aws",
                    "terraform",
                    "docker",
                    "ci/cd",
                ],
                enabled=True,
            ),
            RoleFamily(
                id="software_engineering",
                label="SWE",
                title_patterns=[r"\b(software engineer|swe\b|full[- ]?stack)\b"],
                responsibility_keywords=["python", "java", "typescript"],
                enabled=True,
            ),
            RoleFamily(
                id="backend",
                label="Backend",
                title_patterns=[r"\b(backend|back[- ]end)\b"],
                responsibility_keywords=["backend", "microservices", "postgresql"],
                enabled=True,
            ),
        ]
    )
    cases = [
        (
            "Platform Engineer",
            "Own Kubernetes on AWS with Terraform and Docker CI/CD pipelines.",
            "cloud_platform",
        ),
        (
            "Software Engineer, New Grad",
            "Build product features in Python and TypeScript.",
            "software_engineering",
        ),
        (
            "Backend Engineer",
            "Design microservices and PostgreSQL schemas for customer APIs.",
            "backend",
        ),
        (
            "Member of Technical Staff, Warp Core",
            "Build Kubernetes infrastructure on AWS with Terraform and Docker. "
            "Own platform reliability and CI/CD for internal developer services.",
            "cloud_platform",
        ),
    ]
    for title, desc, expected_family in cases:
        result = classify_job_against_profile(_job(title, desc), profile)
        assert result["status"] == STATUS_MATCHED, title
        assert result["matched_family"] == expected_family, title


def test_layer1_true_negatives_non_software() -> None:
    """Regression: ops/legal/manufacturing titles must not pass on boilerplate."""
    profile = RoleProfile(
        families=[
            RoleFamily(
                id="cloud_platform",
                label="Cloud / Platform",
                title_patterns=[
                    r"\b(platform engineer|cloud engineer|infrastructure engineer)\b"
                ],
                responsibility_keywords=[
                    "kubernetes",
                    "aws",
                    "terraform",
                    "infrastructure",
                    "platform",
                    "docker",
                ],
                enabled=True,
            ),
            RoleFamily(
                id="software_engineering",
                label="SWE",
                title_patterns=[r"\b(software engineer|swe\b)\b"],
                responsibility_keywords=["python", "java", "linux", "software engineer"],
                enabled=True,
            ),
        ]
    )
    # Descriptions intentionally include SWE boilerplate that previously leaked matches.
    boilerplate = (
        "Our company uses AWS, Python, Java, and Linux. We talk about platform "
        "and infrastructure in our engineering blog. Equal opportunity employer."
    )
    negatives = [
        "Office & Events Manager",
        "Labor & Employment Attorney",
        "Accounts Payable Specialist",
        "Lead Avionics Technician",
        "Manufacturing Engineer, Energetics",
        "Mechanical Engineer, Static Structures",
        "Logistics Specialist",
        "EHS Compliance Specialist",
    ]
    for title in negatives:
        result = classify_job_against_profile(_job(title, boilerplate), profile)
        assert result["status"] == STATUS_MISMATCH, title


def test_other_roles_fit_filter() -> None:
    mismatch = _job("Sales Manager", "Sell enterprise software licenses.")
    mismatch.intake_metadata = {
        "role_profile": {
            "status": STATUS_MISMATCH,
            "matched_family": None,
            "reason": "Outside role family: sales",
        },
        "evidence_fit": {
            "band": BAND_RECOMMENDED,
            "score": 90,
            "short_reason": "n/a",
        },
        "eligibility": {"result": "actionable"},
    }
    matched = _job(
        "Backend Engineer",
        "Build Python REST APIs on AWS with Docker and PostgreSQL. "
        "Collaborate with product on backend platform delivery. "
        + (" Reliable software services. " * 20),
    )
    matched.intake_metadata = {
        "role_profile": {
            "status": STATUS_MATCHED,
            "matched_family": "backend",
            "reason": "ok",
        },
        "evidence_fit": {
            "band": BAND_RECOMMENDED,
            "score": 88,
            "short_reason": "ok",
        },
    }

    assert _matches_fit_filter(mismatch, FitFeedFilter.other_roles) is True
    assert _matches_fit_filter(matched, FitFeedFilter.other_roles) is False
    assert _matches_fit_filter(mismatch, FitFeedFilter.recommended) is False
    assert _matches_fit_filter(matched, FitFeedFilter.recommended) is True

    counts = compute_fit_counts([mismatch, matched])
    assert counts.other_roles == 1
    assert counts.recommended == 1

    # JobQueryParams accepts the new enum value.
    params = JobQueryParams(fit=FitFeedFilter.other_roles)
    assert params.fit == FitFeedFilter.other_roles
