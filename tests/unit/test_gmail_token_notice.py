"""Dashboard refresh payload surfaces stale Gmail token notices."""

from __future__ import annotations

from unittest.mock import patch

from packages.core.services.gmail_application_match import gmail_token_status
from packages.core.services.job_refresh_service import get_refresh_status


def test_gmail_token_status_missing_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "packages.core.services.gmail_application_match.CLIENT_SECRETS_PATH",
        tmp_path / "missing_client.json",
    )
    monkeypatch.setattr(
        "packages.core.services.gmail_application_match.TOKEN_PATH",
        tmp_path / "missing_token.json",
    )
    status = gmail_token_status()
    assert status["status"] == "missing_secrets"
    assert status["token_present"] is False


def test_get_refresh_status_includes_gmail_token_issue() -> None:
    fake = {
        "status": "expired",
        "message": "Gmail token expired/invalid.",
        "token_present": True,
    }

    class _Sess:
        pass

    with patch(
        "packages.core.services.gmail_application_match.gmail_token_status",
        return_value=fake,
    ), patch(
        "packages.core.services.job_refresh_service._source_status_snapshot",
        return_value=[],
    ), patch(
        "packages.core.services.job_refresh_service._overall_last_refreshed",
        return_value=None,
    ):
        payload = get_refresh_status(_Sess())  # type: ignore[arg-type]
    assert payload["gmail_token_issue"] is True
    assert payload["gmail_needs_attention"] is True
    assert payload["notice_needed"] is True
    assert payload["gmail_token"]["status"] == "expired"
