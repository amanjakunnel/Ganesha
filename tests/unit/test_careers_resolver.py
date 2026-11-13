"""Unit tests for careers board resolver (no live HTTP / no PII)."""

from __future__ import annotations

import json
from pathlib import Path
import httpx
import pytest

from packages.core.services.board_discovery.careers_resolver import (
    company_slug_variants,
    load_board_resolutions,
    resolve_company_via_careers,
    save_board_resolutions,
    verify_board_belongs_to_company,
    CareersResolveHit,
    _extract_ats_from_html,
    _slug_matches_company,
)
from packages.core.services.board_discovery.company_resolution import (
    BoardCandidate,
    resolve_boards_for_companies,
    resolve_company_boards,
)
from packages.core.services.board_discovery.http_client import BoardHttpClient
from packages.core.services.board_discovery.types import UnresolvedCompany


def test_slug_variants_and_match() -> None:
    variants = company_slug_variants("Databricks Inc")
    assert "databricks" in variants
    assert _slug_matches_company("databricks", "Databricks")
    assert _slug_matches_company("door-dash", "DoorDash") or _slug_matches_company(
        "doordash", "DoorDash"
    )
    assert not _slug_matches_company("stripe", "Databricks")
    # Short / loose substrings must not create duplicate false matches.
    assert not _slug_matches_company("ai", "OpenAI")
    assert not _slug_matches_company("data", "Databricks")


def test_extract_ats_from_html() -> None:
    html = """
    <html><title>Acme Careers</title>
    <a href="https://boards.greenhouse.io/acme/jobs/1">Role</a>
    <a href="https://jobs.lever.co/acme">Lever</a>
    <a href="https://jobs.ashbyhq.com/AcmeCo/role">Ashby</a>
    </html>
    """
    found = _extract_ats_from_html(html)
    platforms = {p for p, _t, _r in found}
    assert "greenhouse" in platforms
    assert "lever" in platforms
    assert "ashby" in platforms


def test_verify_board_slug_match_with_mock_transport() -> None:
    body = json.dumps({"jobs": [{"id": 1, "title": "Eng", "absolute_url": "https://x"}]}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if "boards-api.greenhouse.io" in str(request.url):
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with BoardHttpClient(transport=transport, min_interval=0) as client:
        ref = verify_board_belongs_to_company(
            client,
            platform="greenhouse",
            token="acme",
            company="Acme",
            html_hint="<title>Acme Careers</title>",
        )
    assert ref is not None
    assert ref.board_token == "acme"

    with BoardHttpClient(transport=transport, min_interval=0) as client:
        bad = verify_board_belongs_to_company(
            client,
            platform="greenhouse",
            token="acme",
            company="Totally Different Corp",
        )
    assert bad is None


def test_resolve_cache_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "board_resolutions.json"
    hit = CareersResolveHit(
        board=__import__(
            "packages.core.services.board_discovery.types", fromlist=["BoardRef"]
        ).BoardRef(
            platform="greenhouse",
            board_token="pillar",
            company="Pillar",
        ),
        method="slug_probe",
    )
    save_board_resolutions(
        resolved=[hit],
        unresolved=[UnresolvedCompany(company="NoBoard Co", reason="no_careers_page")],
        path=path,
    )
    data = load_board_resolutions(path)
    assert data["boards"]
    assert data["boards"][0]["board_token"] == "pillar"
    assert data["unresolved"][0]["reason"] == "no_careers_page"


def test_resolve_company_boards_prefers_referral_and_careers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Referral sheet boards + careers fills come before incidental job boards."""
    csv = "Company\nPillar\nDatabricks\n"
    candidates = [
        BoardCandidate(
            platform="greenhouse",
            board_token="unrelatedco",
            region=None,
            company_name="Unrelated Co",
            source_url="https://boards.greenhouse.io/unrelatedco/jobs/1",
        )
    ]

    class FakeSession:
        pass

    def fake_collect(_session: object, **_kwargs: object) -> list[BoardCandidate]:
        return candidates

    def fake_careers(
        session: object,
        companies: list[str],
        *,
        limit: int,
        **_kwargs: object,
    ) -> object:
        from packages.core.services.board_discovery.careers_resolver import (
            CareersResolveBatch,
            CareersResolveHit,
        )
        from packages.core.services.board_discovery.types import BoardRef

        batch = CareersResolveBatch()
        for name in companies[:limit]:
            if name == "Pillar":
                batch.resolved.append(
                    CareersResolveHit(
                        board=BoardRef(
                            platform="ashby",
                            board_token="pillar",
                            company="Pillar",
                        ),
                        method="slug_probe",
                    )
                )
            else:
                batch.unresolved.append(
                    UnresolvedCompany(company=name, reason="no_careers_page")
                )
        return batch

    monkeypatch.setattr(
        "packages.core.services.board_discovery.company_resolution.collect_board_candidates_from_jobs",
        fake_collect,
    )
    monkeypatch.setattr(
        "packages.core.services.board_discovery.careers_resolver.resolve_referral_companies_via_careers",
        fake_careers,
    )

    result = resolve_company_boards(
        FakeSession(),  # type: ignore[arg-type]
        limit=2,
        csv_text=csv,
        use_careers_resolver=True,
    )
    assert result.boards
    assert result.boards[0].company == "Pillar"
    assert result.careers_resolved == 1
    unresolved_names = {u.company for u in result.unresolved}
    assert "Databricks" in unresolved_names


def test_resolve_company_via_careers_cache_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "board_resolutions.json"
    monkeypatch.setattr(
        "packages.core.services.board_discovery.careers_resolver.BOARD_RESOLUTIONS_PATH",
        path,
    )
    from packages.core.services.board_discovery.types import BoardRef

    save_board_resolutions(
        resolved=[
            CareersResolveHit(
                board=BoardRef(
                    platform="lever",
                    board_token="snowflake",
                    company="Snowflake",
                ),
                method="slug_probe",
            )
        ],
        unresolved=[],
        path=path,
    )

    class FakeSession:
        pass

    outcome = resolve_company_via_careers(
        FakeSession(),  # type: ignore[arg-type]
        "Snowflake",
        use_cache=True,
        persist=False,
    )
    assert isinstance(outcome, CareersResolveHit)
    assert outcome.method == "cache"
    assert outcome.board.board_token == "snowflake"


def test_job_url_resolve_still_works() -> None:
    boards, unresolved = resolve_boards_for_companies(
        ["VaultTec", "Unknown"],
        [
            BoardCandidate(
                platform="greenhouse",
                board_token="vaulttec",
                region=None,
                company_name="VaultTec",
                source_url="https://boards.greenhouse.io/vaulttec/jobs/1",
            )
        ],
    )
    assert len(boards) == 1
    assert unresolved[0].reason == "no_board_url_in_job_postings"
