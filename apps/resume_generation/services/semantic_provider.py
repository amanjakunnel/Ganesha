from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from packages.core.settings import settings

PROMPT_VERSION = "semantic-fit-v1"
CACHE_DIR = (
    Path(__file__).resolve().parents[3] / "data" / "resume_corpus" / "semantic_cache"
)


class SemanticProvider(ABC):
    name: str = "none"

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def enrich_strategy(
        self,
        *,
        job_title: str,
        job_description: str,
        track: str,
        keywords: list[str],
        fact_summaries: list[dict[str, Any]],
    ) -> dict[str, Any] | None: ...


class NoneSemanticProvider(SemanticProvider):
    name = "none"

    def available(self) -> bool:
        return False

    def enrich_strategy(self, **_kwargs: Any) -> dict[str, Any] | None:
        return None


def _cache_key(
    *,
    job_description: str,
    evidence_version: str,
    provider: str,
    model: str,
) -> str:
    payload = "|".join(
        [
            hashlib.sha256(job_description.encode()).hexdigest(),
            evidence_version,
            provider,
            model,
            PROMPT_VERSION,
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _validate_semantic_payload(
    data: dict[str, Any],
    allowed_fact_ids: set[str],
) -> dict[str, Any]:
    """Reject invented claims; only accept planning fields over known facts."""
    out: dict[str, Any] = {
        "provider": "anthropic",
        "model": settings.anthropic_model,
        "gaps": [],
        "outline_order": [],
    }
    gaps = data.get("gaps")
    if isinstance(gaps, list):
        out["gaps"] = [g for g in gaps if isinstance(g, str)][:20]

    order = data.get("outline_order") or data.get("selected_fact_ids")
    if isinstance(order, list):
        cleaned = [
            fid for fid in order if isinstance(fid, str) and fid in allowed_fact_ids
        ]
        out["outline_order"] = cleaned

    # Explicitly reject any free-form invented bullets
    for banned in ("new_bullets", "invented_skills", "credentials", "eligibility"):
        if banned in data:
            raise ValueError(f"Semantic payload contained forbidden field: {banned}")
    return out


class AnthropicSemanticProvider(SemanticProvider):
    name = "anthropic"

    def available(self) -> bool:
        return bool((settings.anthropic_api_key or "").strip())

    def enrich_strategy(
        self,
        *,
        job_title: str,
        job_description: str,
        track: str,
        keywords: list[str],
        fact_summaries: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not self.available():
            return None

        allowed_ids = {str(f["id"]) for f in fact_summaries if "id" in f}
        evidence_version = hashlib.sha256(
            json.dumps(sorted(allowed_ids)).encode()
        ).hexdigest()[:16]
        key = _cache_key(
            job_description=job_description,
            evidence_version=evidence_version,
            provider=self.name,
            model=settings.anthropic_model,
        )
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = CACHE_DIR / f"{key}.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return _validate_semantic_payload(cached, allowed_ids)

        # Lazy import so absence of anthropic package or key is fine
        try:
            import urllib.request
        except ImportError:
            return None

        prompt = {
            "task": (
                "Plan a truthful resume outline. Use ONLY the provided fact ids. "
                "Do not invent skills, employers, dates, metrics, or credentials. "
                "Return JSON with keys: gaps (string[]), outline_order (fact id[])."
            ),
            "job_title": job_title,
            "track": track,
            "keywords": keywords[:30],
            "facts": fact_summaries,
        }
        body = json.dumps(
            {
                "model": settings.anthropic_model,
                "max_tokens": 800,
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(prompt),
                    }
                ],
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": settings.anthropic_api_key or "",
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=45) as resp:  # noqa: S310
            raw = json.loads(resp.read().decode("utf-8"))
        text_parts = []
        for block in raw.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text") or "")
        text = "\n".join(text_parts).strip()
        # Extract JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Semantic provider returned non-JSON content")
        parsed = json.loads(text[start : end + 1])
        validated = _validate_semantic_payload(parsed, allowed_ids)
        cache_path.write_text(json.dumps(validated), encoding="utf-8")
        return validated


def get_semantic_provider() -> SemanticProvider:
    name = (settings.semantic_fit_provider or "none").strip().lower()
    if name == "anthropic":
        return AnthropicSemanticProvider()
    return NoneSemanticProvider()


def semantic_status() -> dict[str, str]:
    provider = (settings.semantic_fit_provider or "none").strip().lower()
    key_set = "configured" if (settings.anthropic_api_key or "").strip() else "not set"
    return {
        "provider": provider,
        "model": settings.anthropic_model if provider == "anthropic" else "n/a",
        "api_key": key_set if provider == "anthropic" else "n/a",
    }
