"""Unit tests for Greenhouse / Lever / Ashby board discovery (no live HTTP)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from packages.core.services.board_discovery import ashby, greenhouse, lever
from packages.core.services.board_discovery.company_resolution import (
    BoardCandidate,
    parse_board_from_url,
    resolve_boards_for_companies,
)
from packages.core.services.board_discovery.http_client import BoardHttpClient
from packages.core.services.board_discovery.sync import (
    board_job_to_import_dict,
    mark_missing_board_jobs_closed,
    sync_company_boards,
)
from packages.core.services.board_discovery.types import BoardRef

FIXTURES = Path(__file__).parent / "fixtures" / "boards"


@pytest.fixture
def greenhouse_payload() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / "greenhouse_jobs.json").read_text(encoding="utf-8")
    )
    return data


@pytest.fixture
def lever_payload() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads(
        (FIXTURES / "lever_postings.json").read_text(encoding="utf-8")
    )
    return data


@pytest.fixture
def ashby_payload() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / "ashby_jobs.json").read_text(encoding="utf-8")
    )
    return data


def test_parse_board_urls() -> None:
    assert parse_board_from_url(
        "https://boards.greenhouse.io/vaulttec/jobs/127817"
    ) == ("greenhouse", "vaulttec", None)
    assert parse_board_from_url(
        "https://job-boards.greenhouse.io/Acme/jobs/1"
    ) == ("greenhouse", "acme", None)
    assert parse_board_from_url(
        "https://boards.greenhouse.io/embed/job_app?token=x"
    ) is None
    assert parse_board_from_url("https://jobs.lever.co/Stripe/abc") == (
        "lever",
        "stripe",
        None,
    )
    assert parse_board_from_url("https://jobs.eu.lever.co/eu-co/abc") == (
        "lever",
        "eu-co",
        "eu",
    )
    assert parse_board_from_url("https://jobs.ashbyhq.com/ExampleCo/role") == (
        "ashby",
        "ExampleCo",
        None,
    )


def test_greenhouse_parse_and_validate(greenhouse_payload: dict[str, Any]) -> None:
    assert greenhouse.is_valid_jobs_payload(greenhouse_payload)
    assert not greenhouse.is_valid_jobs_payload({"jobs": "nope"})
    board = BoardRef(
        platform="greenhouse",
        board_token="vaulttec",
        company="VaultTec",
    )
    jobs = greenhouse.parse_jobs(greenhouse_payload, board=board)
    assert len(jobs) == 2
    assert jobs[0].external_id == "127817"
    assert jobs[0].requisition_id == "50"
    assert "vaulttec" in jobs[0].canonical_url
    assert jobs[0].source_import_key == "greenhouse|vaulttec|127817"
    # Prefer first_published over updated_at when both present.
    assert jobs[0].posted_at == "2015-06-01T12:00:00-05:00"
    # Fall back to updated_at when first_published is missing.
    assert jobs[1].posted_at == "2026-01-10T10:00:00-05:00"


def test_greenhouse_prefers_first_published() -> None:
    board = BoardRef(
        platform="greenhouse", board_token="vaulttec", company="VaultTec"
    )
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Both",
                "first_published": "2020-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "absolute_url": "https://boards.greenhouse.io/vaulttec/jobs/1",
                "location": {"name": "US"},
            },
            {
                "id": 2,
                "title": "Updated only",
                "updated_at": "2024-06-01T00:00:00Z",
                "absolute_url": "https://boards.greenhouse.io/vaulttec/jobs/2",
                "location": {"name": "US"},
            },
        ]
    }
    jobs = greenhouse.parse_jobs(payload, board=board)
    assert jobs[0].posted_at == "2020-01-01T00:00:00Z"
    assert jobs[1].posted_at == "2024-06-01T00:00:00Z"


def test_lever_parse_and_validate(lever_payload: list[dict[str, Any]]) -> None:
    assert lever.is_valid_jobs_payload(lever_payload)
    assert not lever.is_valid_jobs_payload({"jobs": []})
    board = BoardRef(platform="lever", board_token="acme", company="Acme")
    jobs = lever.parse_jobs(lever_payload, board=board)
    assert len(jobs) == 2
    assert jobs[0].title == "Backend Engineer"
    assert jobs[0].company_apply_url.endswith("/apply")
    # createdAt ms → ISO
    assert jobs[0].posted_at == "2024-01-01T00:00:00+00:00"
    assert jobs[1].posted_at == "2025-01-01T00:00:00+00:00"


def test_lever_created_at_ms_parsing() -> None:
    board = BoardRef(platform="lever", board_token="acme", company="Acme")
    ms = 1_704_067_200_000  # 2024-01-01T00:00:00Z
    jobs = lever.parse_jobs(
        [
            {
                "id": "x",
                "text": "Role",
                "createdAt": ms,
                "hostedUrl": "https://jobs.lever.co/acme/x",
                "applyUrl": "https://jobs.lever.co/acme/x/apply",
                "descriptionPlain": "US role",
                "categories": {"location": "US"},
            }
        ],
        board=board,
    )
    assert jobs[0].posted_at == "2024-01-01T00:00:00+00:00"


def test_ashby_parse_skips_unlisted(ashby_payload: dict[str, Any]) -> None:
    assert ashby.is_valid_jobs_payload(ashby_payload)
    board = BoardRef(platform="ashby", board_token="exampleco", company="ExampleCo")
    jobs = ashby.parse_jobs(ashby_payload, board=board)
    titles = {j.title for j in jobs}
    assert "Product Manager" in titles
    assert "Software Engineer" in titles
    assert "Hidden Deep Link Role" not in titles
    by_title = {j.title: j for j in jobs}
    assert by_title["Product Manager"].posted_at == "2021-04-30T16:21:55.393+00:00"
    assert by_title["Software Engineer"].posted_at == "2026-02-01T12:00:00.000+00:00"


def test_resolve_boards_for_companies_matches() -> None:
    companies = ["VaultTec", "Unknown Corp", "PLI"]
    candidates = [
        BoardCandidate(
            platform="greenhouse",
            board_token="vaulttec",
            region=None,
            company_name="VaultTec Inc",
            source_url="https://boards.greenhouse.io/vaulttec/jobs/1",
        ),
        # Substring false-positive guard: "pli" appears inside "applied".
        BoardCandidate(
            platform="ashby",
            board_token="applied",
            region=None,
            company_name="Applied Intuition",
            source_url="https://jobs.ashbyhq.com/applied/role",
        ),
    ]
    boards, unresolved = resolve_boards_for_companies(companies, candidates)
    assert len(boards) == 1
    assert boards[0].board_token == "vaulttec"
    unresolved_names = {u.company for u in unresolved}
    assert "Unknown Corp" in unresolved_names
    assert "PLI" in unresolved_names


def test_fetch_board_uses_fixture_via_mock_transport(
    greenhouse_payload: dict[str, Any],
) -> None:
    body = json.dumps(greenhouse_payload).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("User-Agent") == "GaneshaBoardDiscovery/0.1"
        assert "boards-api.greenhouse.io" in str(request.url)
        if request.headers.get("If-None-Match") == '"abc"':
            return httpx.Response(304, headers={"ETag": '"abc"'})
        return httpx.Response(
            200,
            content=body,
            headers={"ETag": '"etag-1"', "Content-Type": "application/json"},
        )

    transport = httpx.MockTransport(handler)
    board = BoardRef(
        platform="greenhouse", board_token="vaulttec", company="VaultTec"
    )
    with BoardHttpClient(min_interval=0, transport=transport) as client:
        ok = greenhouse.fetch_board(client, board)
        assert ok.ok
        assert ok.status_code == 200
        assert len(ok.jobs) == 2
        assert ok.etag == '"etag-1"'

        cached = greenhouse.fetch_board(client, board, etag='"abc"')
        assert cached.ok
        assert cached.not_modified


def test_sync_company_boards_dry_run_no_db_write(
    greenhouse_payload: dict[str, Any],
    lever_payload: list[dict[str, Any]],
    ashby_payload: dict[str, Any],
) -> None:
    sheet_csv = "Company,Name,Position,Team,Location(s),Alternate Location\nVaultTec,,,,,\nAcme,,,,,\nExampleCo,,,,,\n"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "greenhouse" in url:
            return httpx.Response(
                200,
                json=greenhouse_payload,
                headers={"Content-Type": "application/json"},
            )
        if "lever.co" in url:
            return httpx.Response(
                200,
                json=lever_payload,
                headers={"Content-Type": "application/json"},
            )
        if "ashbyhq" in url:
            return httpx.Response(
                200,
                json=ashby_payload,
                headers={"Content-Type": "application/json"},
            )
        return httpx.Response(404)

    session = MagicMock()
    # No existing import keys → treat as new for eligibility tally.
    session.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
        None
    )

    resolution_boards = [
        BoardRef(platform="greenhouse", board_token="vaulttec", company="VaultTec"),
        BoardRef(platform="lever", board_token="acme", company="Acme"),
        BoardRef(platform="ashby", board_token="exampleco", company="ExampleCo"),
    ]

    with (
        patch(
            "packages.core.services.board_discovery.sync.resolve_company_boards",
            return_value=MagicMock(
                companies=["VaultTec", "Acme", "ExampleCo"],
                boards=resolution_boards,
                unresolved=[],
                sheet_error=None,
            ),
        ),
        BoardHttpClient(min_interval=0, transport=httpx.MockTransport(handler)) as client,
    ):
        result = sync_company_boards(
            session,
            dry_run=True,
            limit=3,
            csv_text=sheet_csv,
            client=client,
            prior_etags={},
        )

    assert result.companies_checked == 3
    assert result.boards_ok == 3
    assert result.fetched == 2 + 2 + 2  # ashby skips unlisted
    assert result.new == result.fetched
    assert result.closed == 0
    session.commit.assert_not_called()


def test_board_job_to_import_dict_eligibility_flag(
    greenhouse_payload: dict[str, Any],
) -> None:
    from datetime import UTC, datetime

    board = BoardRef(
        platform="greenhouse", board_token="vaulttec", company="VaultTec"
    )
    job = greenhouse.parse_jobs(greenhouse_payload, board=board)[1]
    data = board_job_to_import_dict(
        job, synced_at=datetime.now(UTC), apply_eligibility=True
    )
    assert "eligibility" in (data.get("intake_metadata") or {})
    data2 = board_job_to_import_dict(
        job, synced_at=datetime.now(UTC), apply_eligibility=False
    )
    assert "eligibility" not in (data2.get("intake_metadata") or {})


def test_sync_per_board_timeout_skips_and_continues(
    greenhouse_payload: dict[str, Any],
) -> None:
    import time

    progress: list[dict[str, Any]] = []

    def slow_fetch(client: Any, board: BoardRef, *, etag: str | None = None) -> Any:
        del client, etag
        time.sleep(0.25)
        from packages.core.services.board_discovery.types import BoardFetchResult

        return BoardFetchResult(board=board, ok=True, status_code=200, jobs=[])

    def fast_fetch(client: Any, board: BoardRef, *, etag: str | None = None) -> Any:
        del client, etag
        from packages.core.services.board_discovery.types import BoardFetchResult

        return BoardFetchResult(
            board=board,
            ok=True,
            status_code=200,
            jobs=greenhouse.parse_jobs(
                greenhouse_payload,
                board=board,
            ),
        )

    session = MagicMock()
    session.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
        None
    )

    boards = [
        BoardRef(platform="greenhouse", board_token="slow", company="SlowCo"),
        BoardRef(platform="greenhouse", board_token="vaulttec", company="VaultTec"),
    ]

    def fake_fetcher(client: Any, board: BoardRef, *, etag: str | None = None) -> Any:
        if board.board_token == "slow":
            return slow_fetch(client, board, etag=etag)
        return fast_fetch(client, board, etag=etag)

    with (
        patch(
            "packages.core.services.board_discovery.sync.resolve_company_boards",
            return_value=MagicMock(
                companies=["SlowCo", "VaultTec"],
                boards=boards,
                unresolved=[],
                sheet_error=None,
            ),
        ),
        patch.dict(
            "packages.core.services.board_discovery.sync._FETCHERS",
            {"greenhouse": fake_fetcher},
            clear=False,
        ),
    ):
        result = sync_company_boards(
            session,
            dry_run=True,
            limit=2,
            prior_etags={},
            board_timeout_seconds=0.05,
            on_board_progress=progress.append,
            client=MagicMock(),
        )

    assert result.boards_attempted == 2
    assert result.boards_ok == 1
    assert any("board_timeout" in e for e in result.errors)
    assert result.fetched == 2
    assert any(p.get("status") == "error" for p in progress)
    assert any(p.get("status") == "ok" and p.get("fetched") == 2 for p in progress)


def test_mark_missing_closed_never_deletes() -> None:
    from packages.core.db import SessionLocal, get_engine
    from packages.core.domain.models import Base, JobPosting, make_uuid

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    keep_key = f"greenhouse|vaulttec|keep|{make_uuid()}"
    gone_key = f"greenhouse|vaulttec|gone|{make_uuid()}"
    # Truncate keys to fit column if needed — source_import_key is 255.
    keep_key = keep_key[:255]
    gone_key = gone_key[:255]
    try:
        kept = JobPosting(
            source="board_api",
            source_name="greenhouse",
            source_type="board_api",
            title="Kept",
            description_text="desc",
            description_hash=f"h-keep|{make_uuid()}",
            dedupe_key=f"d-keep|{make_uuid()}",
            source_import_key=keep_key,
            intake_metadata={},
        )
        gone = JobPosting(
            source="board_api",
            source_name="greenhouse",
            source_type="board_api",
            title="Gone",
            description_text="desc",
            description_hash=f"h-gone|{make_uuid()}",
            dedupe_key=f"d-gone|{make_uuid()}",
            source_import_key=gone_key,
            intake_metadata={},
        )
        session.add_all([kept, gone])
        session.commit()
        board = BoardRef(
            platform="greenhouse", board_token="vaulttec", company="VaultTec"
        )
        closed = mark_missing_board_jobs_closed(
            session, board=board, seen_import_keys={keep_key}
        )
        session.commit()
        assert closed >= 1
        session.refresh(gone)
        assert (gone.intake_metadata or {}).get("simplify_markers", {}).get(
            "listing_closed"
        )
        assert gone.status == "closed"
        assert session.get(JobPosting, gone.id) is not None
        session.refresh(kept)
        assert kept.status != "closed"
    finally:
        session.close()
