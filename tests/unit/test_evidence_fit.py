from __future__ import annotations

from packages.core.domain.models import JobPosting, ResumeFact, make_uuid
from packages.core.services.evidence_fit_service import (
    BAND_LOW_FIT,
    BAND_NEEDS_REVIEW,
    BAND_RECOMMENDED,
    BAND_STRETCH,
    assess_evidence_fit,
    band_for_score,
    detect_domain_mismatch,
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


def _job(title: str, description: str) -> JobPosting:
    return JobPosting(
        id=make_uuid(),
        source="manual",
        title=title,
        description_text=description,
        description_hash="x",
        dedupe_key="y",
        status="new",
    )


def test_strong_backend_match_is_recommended() -> None:
    facts = [
        _fact(
            "Built REST APIs in Python and deployed services on AWS with Docker.",
            skills=["python", "aws", "docker", "api"],
        ),
        _fact(
            "Owned Jenkins CI pipelines and Kubernetes deployments for backend services.",
            skills=["jenkins", "kubernetes"],
        ),
    ]
    job = _job(
        "Software Engineer",
        "\n".join(
            [
                "Requirements",
                "- Build backend APIs in Python",
                "- Deploy on AWS",
                "- Use Docker and Kubernetes",
                "- Collaborate with product teams",
                "Preferred qualifications",
                "- Experience with Go",
            ]
        )
        + (" Backend platform work. " * 20),
    )
    result = assess_evidence_fit(job, facts)
    assert result.band == BAND_RECOMMENDED
    assert result.score >= 70
    assert result.domain_mismatch is False


def test_learnable_framework_gap_is_not_core_mismatch() -> None:
    facts = [
        _fact(
            "Built Python backend services and REST APIs on AWS.",
            skills=["python", "aws", "api"],
        )
    ]
    job = _job(
        "Backend Engineer",
        "Requirements\n- Python backend experience\n- AWS\n- Preferred: NestJS\n"
        + (" API services and cloud delivery. " * 25),
    )
    result = assess_evidence_fit(job, facts)
    assert result.domain_mismatch is False
    assert result.band in {BAND_RECOMMENDED, BAND_STRETCH}


def test_hardware_formal_verification_is_low_fit() -> None:
    facts = [
        _fact(
            "Built Python services on Linux and used AWS and C++ for tooling.",
            skills=["python", "linux", "aws", "c++"],
        )
    ]
    job = _job(
        "Formal Verification Engineer",
        "\n".join(
            [
                "You will own RTL design and verification for ASIC/SoC blocks.",
                "Required: SystemVerilog, SVA, UVM, formal verification tools,",
                "digital design expertise, and Verilog testbenches.",
            ]
        )
        + (" Hardware verification methodology. " * 20),
    )
    result = assess_evidence_fit(job, facts)
    assert result.domain_mismatch is True
    assert result.band == BAND_LOW_FIT
    assert result.score < 50
    assert "rtl" in (result.domain_mismatch_reason or "").lower() or "verification" in (
        result.short_reason or ""
    ).lower()


def test_shared_generic_skills_do_not_rescue_hardware_mismatch() -> None:
    facts = [
        _fact("Python Linux AWS C++ engineering coursework.", skills=["python", "linux", "aws", "c++"])
    ]
    job = _job(
        "ASIC Verification Engineer",
        "RTL Verilog SystemVerilog SVA ASIC SoC formal verification digital design UVM "
        + ("gate-level simulation. " * 30),
    )
    mismatch, _reason = detect_domain_mismatch(job.title, job.description_text, "python linux aws c++")
    assert mismatch is True
    result = assess_evidence_fit(job, facts)
    assert result.band == BAND_LOW_FIT
    assert result.score <= 28


def test_software_formal_methods_is_not_blacklisted() -> None:
    facts = [
        _fact(
            "Built static analysis tooling in Python for distributed systems.",
            skills=["python", "static analysis"],
        )
    ]
    job = _job(
        "Software Engineer, Formal Methods Tooling",
        "Build developer tools and formal methods / static analysis platforms in Python. "
        "Not an RTL hardware role. "
        + ("Compiler and program analysis. " * 20),
    )
    mismatch, _ = detect_domain_mismatch(job.title, job.description_text, "python static analysis")
    assert mismatch is False
    result = assess_evidence_fit(job, facts)
    assert result.band != BAND_LOW_FIT or result.domain_mismatch is False


def test_semiconductor_software_role_not_rejected_by_industry() -> None:
    facts = [
        _fact("Backend Java APIs and cloud services.", skills=["java", "api", "cloud"])
    ]
    job = _job(
        "Software Engineer",
        "Join our semiconductor company building cloud software and Java APIs for customers. "
        + ("Software platform delivery. " * 25),
    )
    mismatch, _ = detect_domain_mismatch(job.title, job.description_text, "java api cloud")
    assert mismatch is False


def test_sparse_jd_is_needs_review() -> None:
    job = _job("Engineer", "Short stub")
    result = assess_evidence_fit(job, [_fact("Python APIs")])
    assert result.band == BAND_NEEDS_REVIEW
    assert result.confidence == "insufficient"


def test_band_boundaries() -> None:
    assert band_for_score(70, domain_mismatch=False, confidence="high") == BAND_RECOMMENDED
    assert band_for_score(69, domain_mismatch=False, confidence="high") == BAND_STRETCH
    assert band_for_score(50, domain_mismatch=False, confidence="high") == BAND_STRETCH
    assert band_for_score(49, domain_mismatch=False, confidence="high") == BAND_LOW_FIT
    assert band_for_score(90, domain_mismatch=True, confidence="high") == BAND_LOW_FIT
    assert (
        band_for_score(90, domain_mismatch=False, confidence="insufficient")
        == BAND_NEEDS_REVIEW
    )


def test_location_line_does_not_inflate_to_perfect_score() -> None:
    facts = [
        _fact(
            "M.S. Computer Science at Penn State University, State College.",
            skills=["python"],
        ),
        _fact(
            "Built Python backend APIs and Kafka consumers on AWS.",
            skills=["python", "aws", "kafka", "api"],
        ),
    ]
    job = _job(
        "Software Developer III",
        "\n".join(
            [
                "We are open to hiring candidates to work from our global headquarters "
                "in State College, PA, USA, or remotely.",
                "Key Responsibilities",
                "Lead the design and development of complex backend software processors.",
                "Own end-to-end backend data flows and monitoring for real-time systems.",
                "Demonstrate skill across relational databases, cloud technologies, and mentoring.",
                "Preferred qualifications",
                "Experience with NestJS",
            ]
        )
        + (" Real-time weather data platforms. " * 15),
    )
    result = assess_evidence_fit(job, facts)
    locationish = [
        r
        for r in result.requirements
        if "state college" in r.requirement.lower() or "remotely" in r.requirement.lower()
    ]
    assert locationish == []
    assert result.score < 100 or len(result.requirements) >= 2


def test_preferred_gap_does_not_force_low_fit() -> None:
    facts = [
        _fact(
            "Python backend APIs on AWS with Docker and Kubernetes.",
            skills=["python", "aws", "docker", "kubernetes", "api"],
        )
    ]
    job = _job(
        "Software Engineer",
        "Requirements\n- Python\n- AWS\n- Docker\n- Kubernetes\nPreferred qualifications\n- Rust\n"
        + (" Cloud backend ownership. " * 20),
    )
    result = assess_evidence_fit(job, facts)
    assert result.band in {BAND_RECOMMENDED, BAND_STRETCH}
    assert result.domain_mismatch is False
