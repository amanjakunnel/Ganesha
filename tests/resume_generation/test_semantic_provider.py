from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from apps.resume_generation.services.semantic_provider import (
    AnthropicSemanticProvider,
    NoneSemanticProvider,
    _validate_semantic_payload,
    get_semantic_provider,
)


def test_none_provider_fallback(monkeypatch) -> None:
    monkeypatch.setenv("SEMANTIC_FIT_PROVIDER", "none")
    # settings already loaded — patch settings object
    from packages.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "semantic_fit_provider", "none")
    provider = get_semantic_provider()
    assert isinstance(provider, NoneSemanticProvider)
    assert provider.available() is False
    assert (
        provider.enrich_strategy(
            job_title="x",
            job_description="y",
            track="dev",
            keywords=[],
            fact_summaries=[],
        )
        is None
    )


def test_validate_rejects_forbidden_fields() -> None:
    try:
        _validate_semantic_payload(
            {"gaps": [], "new_bullets": ["invented"]},
            {"a"},
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "forbidden" in str(exc).lower()


def test_validate_filters_unknown_fact_ids() -> None:
    out = _validate_semantic_payload(
        {"gaps": ["missing k8s"], "outline_order": ["a", "unknown"]},
        {"a"},
    )
    assert out["outline_order"] == ["a"]
    assert out["gaps"] == ["missing k8s"]


def test_anthropic_unavailable_without_key(monkeypatch) -> None:
    from packages.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", None)
    provider = AnthropicSemanticProvider()
    assert provider.available() is False


def test_semantic_cache_roundtrip(tmp_path: Path, monkeypatch) -> None:
    from packages.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "test-key-not-real")
    monkeypatch.setattr(
        "apps.resume_generation.services.semantic_provider.CACHE_DIR",
        tmp_path,
    )
    provider = AnthropicSemanticProvider()
    cached = {"gaps": ["gap1"], "outline_order": ["f1"]}
    # Pre-seed cache by computing key path via enrich after mocking network
    facts = [{"id": "f1", "text": "Python APIs", "section": "experience"}]

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            body = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(cached),
                    }
                ]
            }
            return json.dumps(body).encode()

    with patch("urllib.request.urlopen", return_value=FakeResp()):
        first = provider.enrich_strategy(
            job_title="Engineer",
            job_description="Python backend",
            track="dev",
            keywords=["python"],
            fact_summaries=facts,
        )
    assert first is not None
    # Second call should hit cache (urlopen must not be required)
    with patch("urllib.request.urlopen", side_effect=AssertionError("cache miss")):
        second = provider.enrich_strategy(
            job_title="Engineer",
            job_description="Python backend",
            track="dev",
            keywords=["python"],
            fact_summaries=facts,
        )
    assert second == first
