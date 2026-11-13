"""Refresh status exposes stale + Gmail token notice fields."""

from __future__ import annotations

from packages.core.services.job_refresh_service import get_refresh_status


def test_refresh_status_includes_gmail_notice_fields(monkeypatch: object) -> None:
    from packages.core.services import job_refresh_service as jrs
    from packages.core.services import gmail_application_match as gam

    monkeypatch.setattr(  # type: ignore[attr-defined]
        gam,
        "gmail_token_status",
        lambda: {
            "status": "expired",
            "message": "Gmail token expired/invalid.",
            "token_present": True,
        },
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        jrs,
        "_source_status_snapshot",
        lambda _session: [],
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        jrs,
        "_overall_last_refreshed",
        lambda _session, _last: None,
    )

    class _Sess:
        pass

    payload = get_refresh_status(_Sess())  # type: ignore[arg-type]
    assert payload["gmail_token"]["status"] == "expired"
    assert payload["gmail_token_issue"] is True
    assert payload["gmail_needs_attention"] is True
    assert payload["notice_needed"] is True
    assert "stale_threshold_hours" in payload
