from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from packages.core.domain.models import AuditEvent, Company, JobPosting
from packages.core.services.company_normalize import canonical_company_display, normalize_company_key
from packages.core.services.job_dedupe import find_duplicate_job
from packages.core.domain.models import make_description_hash, make_dedupe_key
from packages.core.services.job_ranking import detect_intake_flags


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


def get_or_create_company(session: Session, name: str) -> Company | None:
    return _get_or_create_company(session, name)


def _get_or_create_company(session: Session, name: str) -> Company | None:
    name_norm = canonical_company_display(name)
    if not name_norm:
        return None
    nkey = normalize_company_key(name_norm)
    existing = (
        session.query(Company)
        .filter(
            (Company.canonical_name == name_norm) | (Company.normalized_key == nkey)
        )
        .one_or_none()
    )
    if existing:
        if not existing.normalized_key:
            existing.normalized_key = nkey
            session.add(existing)
        return existing
    c = Company(canonical_name=name_norm, normalized_key=nkey)
    session.add(c)
    session.flush()
    return c


def upsert_job_from_import(
    session: Session,
    data: dict[str, Any],
    *,
    source_name: str,
    source_type: str,
    legacy_source: str | None = None,
) -> tuple[str, bool]:
    """Insert or update a job from a structured import row.

    Returns (job_id, created_new).
    """
    dup = find_duplicate_job(session, {**data, "source_name": source_name})
    if dup.is_duplicate and dup.existing_id:
        existing = session.query(JobPosting).filter(JobPosting.id == dup.existing_id).one()
        _touch_import(existing, data, source_name, source_type, dup.reasons)
        session.add(existing)
        ev = AuditEvent(
            entity_type="job_posting",
            entity_id=existing.id,
            event_type="import_duplicate_seen",
            payload={"reasons": dup.reasons, "source_name": source_name},
        )
        session.add(ev)
        session.flush()
        return existing.id, False

    company = _get_or_create_company(session, data["company"])
    title = data["title"]
    description_text = data["description_text"]
    flags = detect_intake_flags(title, description_text)

    jp = JobPosting(
        source=legacy_source or source_type,
        source_name=source_name,
        source_type=source_type,
        external_id=data.get("external_id"),
        canonical_url=data.get("canonical_url"),
        company_apply_url=data.get("company_apply_url"),
        title=title,
        company_id=company.id if company else None,
        location=data.get("location"),
        workplace_type=data.get("workplace_type"),
        employment_type=data.get("employment_type"),
        description_text=description_text,
        posted_at=_parse_date(data.get("posted_at")),
        scraped_at=_parse_date(data.get("scraped_at")),
        status="new",
        normalized_title=title.lower().strip(),
        description_hash=make_description_hash(description_text),
        dedupe_key=make_dedupe_key(data["company"], title, data.get("location")),
        raw_payload=data.get("raw_payload"),
        intake_metadata={"flags": flags, **(data.get("intake_metadata") or {})},
        source_import_key=data.get("source_import_key"),
    )
    session.add(jp)
    session.flush()
    ev = AuditEvent(
        entity_type="job_posting",
        entity_id=jp.id,
        event_type="ingested",
        payload={"source_name": source_name, "source_type": source_type},
    )
    session.add(ev)
    session.flush()
    return jp.id, True


def _touch_import(
    jp: JobPosting,
    data: dict[str, Any],
    source_name: str,
    source_type: str,
    reasons: list[str],
) -> None:
    if data.get("scraped_at"):
        jp.scraped_at = _parse_date(data.get("scraped_at"))
    if data.get("posted_at") and not jp.posted_at:
        jp.posted_at = _parse_date(data.get("posted_at"))
    if data.get("company_apply_url") and not jp.company_apply_url:
        jp.company_apply_url = data.get("company_apply_url")
    meta = dict(jp.intake_metadata or {})
    meta["last_import"] = {
        "source_name": source_name,
        "source_type": source_type,
        "duplicate_reasons": reasons,
    }
    jp.intake_metadata = meta


def _parse_date(val: Any) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None
