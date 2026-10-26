from __future__ import annotations

from apps.cover_letter_generation.paths import COMPANY_EVIDENCE_UNAVAILABLE
from apps.cover_letter_generation.services.company_research_service import (
    is_disallowed_source,
    research_company,
)


def test_official_about_page_is_saved() -> None:
    seen: list[str] = []

    def transport(url: str) -> tuple[int, str]:
        seen.append(url)
        if url.endswith("/careers"):
            raise TimeoutError("careers unavailable")
        html = (
            "<title>About Northwind</title><p>"
            "Northwind Labs publishes developer documentation for forecast APIs "
            "used by its own product teams."
            "</p>"
        )
        return 200, html

    evidence, warnings = research_company(
        company_name="Northwind Labs",
        website_domain="northwind.example",
        transport=transport,
    )
    assert evidence
    assert evidence[0].source_type == "about"
    assert evidence[0].url.startswith("https://northwind.example/")
    assert "forecast APIs" in evidence[0].excerpt
    assert not any("http://" in item.excerpt for item in evidence)
    assert seen


def test_linkedin_and_other_non_official_sources_are_rejected() -> None:
    called = False

    def transport(_url: str) -> tuple[int, str]:
        nonlocal called
        called = True
        return 200, "<p>Someone on the internet said this company is a market leader.</p>"

    evidence, warnings = research_company(
        company_name="Northwind Labs",
        apply_url="https://www.linkedin.com/jobs/view/1",
        transport=transport,
    )
    assert evidence == []
    assert not called
    assert COMPANY_EVIDENCE_UNAVAILABLE in warnings
    assert is_disallowed_source("https://www.reddit.com/r/jobs")


def test_research_failure_returns_warning_without_raising() -> None:
    def transport(_url: str) -> tuple[int, str]:
        raise ConnectionError("offline")

    evidence, warnings = research_company(
        company_name="Northwind Labs",
        website_domain="northwind.example",
        transport=transport,
    )
    assert evidence == []
    assert COMPANY_EVIDENCE_UNAVAILABLE in warnings
