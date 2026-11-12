from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from packages.core.domain.models import AuditEvent, Company, JobPosting
from packages.core.services.company_normalize import (
    canonical_company_display,
    normalize_company_key,
)
from packages.core.services.job_dedupe import find_duplicate_job
from packages.core.domain.models import make_description_hash, make_dedupe_key
from packages.core.services.job_eligibility import (
    attach_eligibility_to_metadata,
    classify_job_eligibility,
)
from packages.core.services.job_ranking import detect_intake_flags


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


def get_or_create_company(session: Session, name: str) -> Company | None:
    company, _warning = _get_or_create_company(session, name)
    return company


def _get_or_create_company(
    session: Session, name: str
) -> tuple[Company | None, str | None]:
    name_norm = canonical_company_display(name)
    if not name_norm:
        return None, None
    nkey = normalize_company_key(name_norm)
    matches = (
        session.query(Company)
        .filter(
            (Company.canonical_name == name_norm) | (Company.normalized_key == nkey)
        )
        .order_by(Company.created_at.asc(), Company.id.asc())
        .all()
    )
    warning = f"company_duplicate:{name_norm}" if len(matches) > 1 else None
    if matches:
        existing = matches[0]
        if not existing.normalized_key:
            existing.normalized_key = nkey
            session.add(existing)
        return existing, warning
    c = Company(canonical_name=name_norm, normalized_key=nkey)
    session.add(c)
    session.flush()
    return c, None


def upsert_job_from_import(
    session: Session,
    data: dict[str, Any],
    *,
    source_name: str,
    source_type: str,
    legacy_source: str | None = None,
    reclassify_on_update: bool = True,
    warnings_out: list[str] | None = None,
) -> tuple[str, bool]:
    """Insert or update a job from a structured import row.

    Returns (job_id, created_new).

    When ``reclassify_on_update`` is False, duplicate touches refresh scrape
    metadata but leave existing eligibility decisions unchanged (used by board
    discovery so eligibility applies to newly created rows only).
    """
    dup = find_duplicate_job(session, {**data, "source_name": source_name})
    if warnings_out is not None:
        for warn in dup.warnings:
            if warn not in warnings_out:
                warnings_out.append(warn)
    if dup.is_duplicate and dup.existing_id:
        existing = (
            session.query(JobPosting).filter(JobPosting.id == dup.existing_id).one()
        )
        _touch_import(
            existing,
            data,
            source_name,
            source_type,
            dup.reasons,
            reclassify_eligibility=reclassify_on_update,
        )
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

    company, company_warning = _get_or_create_company(session, data["company"])
    if company_warning and warnings_out is not None and company_warning not in warnings_out:
        warnings_out.append(company_warning)
    title = data["title"]
    description_text = data["description_text"]
    flags = detect_intake_flags(title, description_text)
    simplify_markers = (data.get("intake_metadata") or {}).get("simplify_markers")
    eligibility = classify_job_eligibility(
        title=title,
        description=description_text,
        company_apply_url=data.get("company_apply_url"),
        location=data.get("location"),
        employment_type=data.get("employment_type"),
        intake_flags=flags,
        source_markers=simplify_markers,
    )
    intake_meta = attach_eligibility_to_metadata(
        {"flags": flags, **(data.get("intake_metadata") or {})},
        eligibility,
    )

    now = datetime.now(UTC)
    # Set first-seen in Python so a skewed Postgres clock cannot backdate discovered_at.
    intake_meta = {
        **intake_meta,
        "first_seen_source": "python",
        "first_seen_approx": False,
        "first_seen_unknown": False,
    }
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
        scraped_at=_parse_date(data.get("scraped_at")) or now,
        discovered_at=now,
        status="new",
        normalized_title=title.lower().strip(),
        description_hash=make_description_hash(description_text),
        dedupe_key=make_dedupe_key(data["company"], title, data.get("location")),
        raw_payload=data.get("raw_payload"),
        intake_metadata=intake_meta,
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
    *,
    reclassify_eligibility: bool = True,
) -> None:
    if data.get("scraped_at"):
        jp.scraped_at = _parse_date(data.get("scraped_at"))
    if data.get("posted_at") and not jp.posted_at:
        jp.posted_at = _parse_date(data.get("posted_at"))
    if data.get("company_apply_url") and not jp.company_apply_url:
        jp.company_apply_url = data.get("company_apply_url")
    flags = detect_intake_flags(jp.title, jp.description_text or "")
    meta = dict(jp.intake_metadata or {})
    meta["flags"] = flags
    meta.update(
        {
            k: v
            for k, v in (data.get("intake_metadata") or {}).items()
            if k != "eligibility"
        }
    )
    if reclassify_eligibility:
        simplify_markers = (data.get("intake_metadata") or {}).get("simplify_markers")
        eligibility = classify_job_eligibility(
            title=jp.title,
            description=jp.description_text or "",
            company_apply_url=jp.company_apply_url,
            location=data.get("location") or jp.location,
            employment_type=data.get("employment_type") or jp.employment_type,
            intake_flags=flags,
            source_markers=simplify_markers,
        )
        meta = attach_eligibility_to_metadata(meta, eligibility)
    meta["last_import"] = {
        "source_name": source_name,
        "source_type": source_type,
        "duplicate_reasons": reasons,
    }
    jp.intake_metadata = meta


def _parse_date(val: Any) -> datetime | None:
    if not val and val != 0:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, (int, float)):
        n = float(val)
        # Milliseconds epoch (Lever createdAt) vs seconds.
        seconds = n / 1000.0 if n >= 1e11 else n
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(val).strip()
    if not s:
        return None
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return _parse_date(int(s))
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None
