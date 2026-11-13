"""User "Not relevant" overrides for Layer-1 role classification.

Persisted at ~/.ganesha/role_not_relevant.json (outside the repo).
Never deletes jobs — marks them Other roles and teaches the classifier
via job id / optional title-pattern overrides.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from packages.core.domain.models import JobPosting

OVERRIDES_DIR = Path.home() / ".ganesha"
OVERRIDES_PATH = OVERRIDES_DIR / "role_not_relevant.json"
OVERRIDES_VERSION = 1
REASON_USER_NOT_RELEVANT = "user_not_relevant"


@dataclass
class RelevanceOverride:
    kind: str  # job_id | title_pattern
    value: str
    created_at: str = ""
    source_job_id: str | None = None
    source_title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RelevanceOverride:
        return cls(
            kind=str(raw.get("kind") or "job_id"),
            value=str(raw.get("value") or ""),
            created_at=str(raw.get("created_at") or ""),
            source_job_id=(
                str(raw["source_job_id"]) if raw.get("source_job_id") else None
            ),
            source_title=(
                str(raw["source_title"]) if raw.get("source_title") else None
            ),
        )


@dataclass
class RelevanceOverrides:
    version: int = OVERRIDES_VERSION
    overrides: list[RelevanceOverride] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "overrides": [o.to_dict() for o in self.overrides],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> RelevanceOverrides:
        if not isinstance(raw, dict):
            return cls()
        items: list[RelevanceOverride] = []
        for item in raw.get("overrides") or []:
            if isinstance(item, dict) and item.get("value"):
                items.append(RelevanceOverride.from_dict(item))
        return cls(
            version=int(raw.get("version") or OVERRIDES_VERSION),
            overrides=items,
        )


def _load_raw(path: Path = OVERRIDES_PATH) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_raw(payload: dict[str, Any], path: Path = OVERRIDES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_relevance_overrides(path: Path | None = None) -> RelevanceOverrides:
    return RelevanceOverrides.from_dict(_load_raw(path or OVERRIDES_PATH))


def save_relevance_overrides(
    overrides: RelevanceOverrides, *, path: Path | None = None
) -> None:
    _save_raw(overrides.to_dict(), path or OVERRIDES_PATH)


def override_matches_job(
    job: JobPosting, overrides: RelevanceOverrides | None = None
) -> RelevanceOverride | None:
    """Return the first override that applies to this job, if any."""
    resolved = overrides or load_relevance_overrides()
    title = job.title or ""
    for item in resolved.overrides:
        if not item.value:
            continue
        if item.kind == "job_id" and item.value == job.id:
            return item
        if item.kind == "title_pattern":
            try:
                if re.search(item.value, title, flags=re.I):
                    return item
            except re.error:
                if item.value.lower() in title.lower():
                    return item
    return None


def append_override(
    override: RelevanceOverride,
    *,
    path: Path | None = None,
) -> RelevanceOverrides:
    """Append override if not already present (idempotent on kind+value)."""
    resolved_path = path or OVERRIDES_PATH
    current = load_relevance_overrides(resolved_path)
    key = (override.kind, override.value.lower())
    existing = {(o.kind, o.value.lower()) for o in current.overrides}
    if key not in existing:
        if not override.created_at:
            override.created_at = datetime.now(UTC).isoformat()
        current.overrides.append(override)
        save_relevance_overrides(current, path=resolved_path)
    return current


def mark_job_not_relevant(
    session: Session,
    job: JobPosting,
    *,
    also_title_pattern: bool = False,
    path: Path | None = None,
) -> dict[str, Any]:
    """Mark job Other roles + persist override. Never deletes the job."""
    from packages.core.services.role_profile import (
        STATUS_MISMATCH,
        store_role_classification,
    )

    resolved_path = path or OVERRIDES_PATH
    store_role_classification(
        job,
        {
            "matched_family": None,
            "status": STATUS_MISMATCH,
            "reason": REASON_USER_NOT_RELEVANT,
            "user_override": True,
        },
    )
    session.add(job)

    appended: list[dict[str, Any]] = []
    job_ov = RelevanceOverride(
        kind="job_id",
        value=job.id,
        source_job_id=job.id,
        source_title=job.title,
    )
    append_override(job_ov, path=resolved_path)
    appended.append(job_ov.to_dict())

    if also_title_pattern and (job.title or "").strip():
        pattern = rf"^{re.escape(job.title.strip())}$"
        title_ov = RelevanceOverride(
            kind="title_pattern",
            value=pattern,
            source_job_id=job.id,
            source_title=job.title,
        )
        append_override(title_ov, path=resolved_path)
        appended.append(title_ov.to_dict())

    session.flush()
    return {
        "job_id": job.id,
        "status": STATUS_MISMATCH,
        "reason": REASON_USER_NOT_RELEVANT,
        "overrides_appended": appended,
        "deleted": False,
    }
