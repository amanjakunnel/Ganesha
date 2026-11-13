"""True-positive / true-negative fixtures for Layer-1 role classification."""

from __future__ import annotations

from pathlib import Path

from packages.core.domain.models import JobPosting, make_uuid
from packages.core.services.role_profile import (
    STATUS_MATCHED,
    STATUS_MISMATCH,
    RoleFamily,
    RoleProfile,
    classify_job_against_profile,
)
from packages.core.services.role_relevance_overrides import (
    REASON_USER_NOT_RELEVANT,
    RelevanceOverride,
    mark_job_not_relevant,
    override_matches_job,
    save_relevance_overrides,
    RelevanceOverrides,
)


def _job(title: str, description: str = "") -> JobPosting:
    return JobPosting(
        id=make_uuid(),
        source="manual",
        title=title,
        description_text=description,
        description_hash="x",
        dedupe_key=make_uuid(),
        status="new",
        location="Remote",
        company_apply_url="https://example.com/apply",
        intake_metadata={},
    )


def _eng_profile() -> RoleProfile:
    return RoleProfile(
        families=[
            RoleFamily(
                id="software_engineering",
                label="SWE",
                title_patterns=[
                    r"\b(software engineer|software developer|swe\b|full[- ]?stack|"
                    r"application engineer|software development engineer)\b"
                ],
                responsibility_keywords=[
                    "software engineer",
                    "python",
                    "java",
                    "typescript",
                    "javascript",
                    "linux",
                ],
                enabled=True,
            ),
            RoleFamily(
                id="backend",
                label="Backend",
                title_patterns=[r"\b(backend|back[- ]end|api engineer)\b"],
                responsibility_keywords=[
                    "backend",
                    "microservices",
                    "grpc",
                    "postgresql",
                    "api",
                    "rest",
                ],
                enabled=True,
            ),
            RoleFamily(
                id="cloud_platform",
                label="Platform",
                title_patterns=[
                    r"\b(platform engineer|cloud engineer|infrastructure engineer|"
                    r"sre\b|site reliability|devops engineer)\b"
                ],
                responsibility_keywords=[
                    "kubernetes",
                    "aws",
                    "terraform",
                    "docker",
                    "ci/cd",
                    "infrastructure",
                    "platform",
                ],
                enabled=True,
            ),
            RoleFamily(
                id="ml_systems",
                label="ML",
                title_patterns=[
                    r"\b(machine learning engineer|ml engineer|mlops|ai engineer)\b"
                ],
                responsibility_keywords=[
                    "pytorch",
                    "tensorflow",
                    "mlops",
                    "inference",
                    "model",
                    "training",
                ],
                enabled=True,
            ),
        ]
    )


def test_true_positives_swe_backend_platform() -> None:
    profile = _eng_profile()
    cases = [
        (
            "Software Engineer II",
            "Build Python services and REST APIs on AWS.",
            {"software_engineering"},
        ),
        (
            "Backend Engineer",
            "Own microservices, gRPC, and PostgreSQL data planes.",
            {"backend"},
        ),
        (
            "Platform Engineer",
            "Operate Kubernetes clusters with Terraform and CI/CD.",
            {"cloud_platform"},
        ),
    ]
    for title, desc, expected_families in cases:
        result = classify_job_against_profile(_job(title, desc), profile)
        assert result["status"] == STATUS_MATCHED, title
        assert result["matched_family"] in expected_families, title


def test_true_negatives_sales_recruiting_finance_fpga() -> None:
    profile = _eng_profile()
    cases = [
        (
            "Account Executive — Enterprise Sales",
            "Drive quota and pipeline generation. Meet customers.",
            "sales",
        ),
        (
            "Sales Development Representative",
            "Outbound SDR prospecting and pipeline generation.",
            "sales",
        ),
        (
            "Talent Strategist",
            "Partner with hiring managers on talent acquisition.",
            "recruit",
        ),
        (
            "Financial Analyst",
            "Build FP&A models and variance analysis for finance.",
            "finance",
        ),
        (
            "Legal Counsel",
            "Advise product on contracts and privacy counsel matters.",
            "legal",
        ),
        (
            "Buyer",
            "Manage purchasing and procurement of components.",
            "procure",
        ),
        (
            "FPGA Design Engineer",
            "RTL Verilog VHDL digital design without software stack.",
            "hardware",
        ),
    ]
    for title, desc, needle in cases:
        result = classify_job_against_profile(_job(title, desc), profile)
        assert result["status"] == STATUS_MISMATCH, title
        assert needle in (result.get("reason") or "").lower() or (
            "non-software" in (result.get("reason") or "").lower()
        ), (title, result.get("reason"))


def test_unfamiliar_title_with_strong_description_still_passes() -> None:
    profile = _eng_profile()
    job = _job(
        "Member of Technical Staff, Warp Core",
        "Build Kubernetes infrastructure on AWS with Terraform and Docker. "
        "Own platform reliability and CI/CD for internal developer services. "
        "software engineer collaboration on backend microservices.",
    )
    result = classify_job_against_profile(job, profile)
    assert result["status"] == STATUS_MATCHED
    assert result["matched_family"] in {"cloud_platform", "backend", "software_engineering"}


def test_not_relevant_override_checked_first(tmp_path: Path) -> None:
    profile = _eng_profile()
    job = _job(
        "Software Engineer",
        "Build Python backend APIs with PostgreSQL and microservices.",
    )
    assert classify_job_against_profile(job, profile)["status"] == STATUS_MATCHED

    path = tmp_path / "role_not_relevant.json"
    overrides = RelevanceOverrides(
        overrides=[
            RelevanceOverride(kind="job_id", value=job.id, source_title=job.title)
        ]
    )
    save_relevance_overrides(overrides, path=path)
    from packages.core.services import role_relevance_overrides as ov

    old = ov.OVERRIDES_PATH
    ov.OVERRIDES_PATH = path
    try:
        assert override_matches_job(job) is not None
        result = classify_job_against_profile(job, profile)
        assert result["status"] == STATUS_MISMATCH
        assert result["reason"] == REASON_USER_NOT_RELEVANT
        assert result.get("user_override") is True
    finally:
        ov.OVERRIDES_PATH = old


class _FakeSession:
    def add(self, *_a: object, **_k: object) -> None:
        return None

    def flush(self) -> None:
        return None


def test_mark_job_not_relevant_persists_status(tmp_path: Path) -> None:
    job = _job("Buyer", "Procurement.")
    path = tmp_path / "role_not_relevant.json"
    result = mark_job_not_relevant(
        _FakeSession(),  # type: ignore[arg-type]
        job,
        path=path,
    )
    assert result["deleted"] is False
    assert result["reason"] == REASON_USER_NOT_RELEVANT
    block = (job.intake_metadata or {}).get("role_profile") or {}
    assert block["status"] == STATUS_MISMATCH
    assert block["reason"] == REASON_USER_NOT_RELEVANT
