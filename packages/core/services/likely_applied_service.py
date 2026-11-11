"""Likely-applied review candidates from Simplify evidence (never auto-promote)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from packages.core.domain.models import AuditEvent, SimplifyHistoryRecord
from packages.core.services.importers.simplify_tracker_csv import (
    canonical_job_url,
    is_ambiguous_greenhouse_embed,
)

# Conservative notes phrases → evidence type "notes_phrase".
NOTES_APPLIED_PHRASES: tuple[str, ...] = (
    r"\bapplied\b",
    r"\bsubmitted (my )?application\b",
    r"\bapplication submitted\b",
    r"\bi applied\b",
    r"\bsent application\b",
)

PROVENANCE_USER_CONFIRM = "user_confirmation_of_simplify_evidence"
REJECT_FLAG = "likely_applied_rejected"
CONFIRM_FLAG = "likely_applied_confirmed"

# Simplify exports "N/A" into Applied Date for many non-applied rows.
APPLIED_DATE_PLACEHOLDERS = frozenset(
    {"", "n/a", "na", "none", "null", "-", "—", "not available"}
)

STRONG_EVIDENCE_KINDS = frozenset(
    {"matching_applied_record", "notes_phrase", "gmail_confirmation"}
)

# Host/path patterns that collapse many unrelated jobs after query stripping.
GENERIC_PORTAL_PATHS = frozenset(
    {
        "/portalcareer",
        "/careers",
        "/career",
        "/jobs",
        "/job",
        "/apply",
        "/",
    }
)


@dataclass
class LikelyAppliedEvidence:
    kind: str  # applied_date | matching_applied_record | notes_phrase | gmail_confirmation
    detail: str
    strength: str = "weak"  # weak | strong
    match_basis: str | None = None  # e.g. canonical_url+company


@dataclass
class LikelyAppliedCandidate:
    record_id: str
    company: str
    role: str
    job_url: str | None
    status_mapped: str | None
    applied_date_text: str | None
    evidence: list[LikelyAppliedEvidence] = field(default_factory=list)
    low_confidence: bool = False
    strength: str = "weak"  # overall: strong if any strong evidence
    group: str = "weak"  # strong | weak

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "company": self.company,
            "role": self.role,
            "job_url": self.job_url,
            "status_mapped": self.status_mapped,
            "applied_date_text": self.applied_date_text,
            "evidence": [asdict(e) for e in self.evidence],
            "low_confidence": self.low_confidence,
            "strength": self.strength,
            "group": self.group,
        }


def is_meaningful_applied_date(value: str | None) -> bool:
    """True only for real-looking Applied Date values (not Simplify placeholders)."""
    text = (value or "").strip()
    if not text:
        return False
    if text.lower() in APPLIED_DATE_PLACEHOLDERS:
        return False
    return True


def _notes_match(notes: str | None) -> str | None:
    if not notes:
        return None
    text = notes.strip()
    if not text:
        return None
    for pattern in NOTES_APPLIED_PHRASES:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return pattern
    return None


def _norm_company(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _companies_compatible(a: str | None, b: str | None) -> bool:
    na, nb = _norm_company(a), _norm_company(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _is_generic_portal_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "/").rstrip("/") or "/"
    host = parsed.netloc.lower()
    if path.lower() in GENERIC_PORTAL_PATHS:
        return True
    # Bare career portals with no job/req id in the path.
    if "successfactors" in host and "job" not in path.lower():
        return True
    if path.count("/") <= 1:
        return True
    return False


def _applied_url_index(
    session: Session,
) -> dict[str, list[SimplifyHistoryRecord]]:
    index: dict[str, list[SimplifyHistoryRecord]] = {}
    rows = (
        session.query(SimplifyHistoryRecord)
        .filter(SimplifyHistoryRecord.status_mapped == "applied")
        .all()
    )
    for row in rows:
        if is_ambiguous_greenhouse_embed(row.job_url or row.job_url_raw):
            continue
        url = canonical_job_url(row.job_url) or canonical_job_url(row.job_url_raw)
        if url:
            index.setdefault(url, []).append(row)
    return index


def _matching_applied_evidence(
    row: SimplifyHistoryRecord,
    applied_urls: dict[str, list[SimplifyHistoryRecord]],
) -> LikelyAppliedEvidence | None:
    """Same canonical URL as an APPLIED row, with company compatibility check.

    Precedence keeps distinct SAVED vs APPLIED import keys, so both rows can
    exist for one posting. Match basis is canonical URL (+ company when
    compatible). Generic portal URLs without a job id are not treated as strong.
    """
    url = canonical_job_url(row.job_url) or canonical_job_url(row.job_url_raw)
    if not url or url not in applied_urls:
        return None
    siblings = applied_urls[url]
    company_matches = [
        s for s in siblings if _companies_compatible(row.company_name, s.company_name)
    ]
    if _is_generic_portal_url(url) and not company_matches:
        return None
    if not company_matches:
        # URL collision across companies — weak, low-confidence signal only.
        other = siblings[0]
        return LikelyAppliedEvidence(
            kind="matching_applied_record",
            detail=(
                f"Same canonical URL as APPLIED row for a different company "
                f"({other.company_name} / {other.role_title}). "
                f"Likely URL-collapse false positive."
            ),
            strength="weak",
            match_basis="canonical_url_only_company_mismatch",
        )
    other = company_matches[0]
    generic = _is_generic_portal_url(url)
    return LikelyAppliedEvidence(
        kind="matching_applied_record",
        detail=(
            f"Same canonical URL as APPLIED record "
            f"{other.company_name} / {other.role_title}"
            + (" (generic portal path; company also matched)" if generic else "")
        ),
        strength="weak" if generic else "strong",
        match_basis=(
            "canonical_url+company_generic_portal"
            if generic
            else "canonical_url+company"
        ),
    )


def list_likely_applied(session: Session) -> list[LikelyAppliedCandidate]:
    """Saved-only rows with Simplify/Gmail evidence they may actually be applied."""
    applied_urls = _applied_url_index(session)
    candidates: list[LikelyAppliedCandidate] = []
    rows = (
        session.query(SimplifyHistoryRecord)
        .filter(SimplifyHistoryRecord.status_mapped == "saved_only")
        .all()
    )
    for row in rows:
        if is_ambiguous_greenhouse_embed(row.job_url or row.job_url_raw):
            continue
        payload = dict(row.payload_json or {})
        if payload.get(REJECT_FLAG) or payload.get(CONFIRM_FLAG):
            continue
        evidence: list[LikelyAppliedEvidence] = []
        low = False

        if is_meaningful_applied_date(row.applied_date_text):
            evidence.append(
                LikelyAppliedEvidence(
                    kind="applied_date",
                    detail=f"Applied Date field = {row.applied_date_text.strip()}",
                    strength="weak",
                    match_basis="simplify_applied_date_text",
                )
            )

        match_ev = _matching_applied_evidence(row, applied_urls)
        if match_ev is not None:
            evidence.append(match_ev)
            if match_ev.match_basis == "canonical_url_only_company_mismatch":
                low = True

        phrase = _notes_match(row.notes)
        if phrase:
            evidence.append(
                LikelyAppliedEvidence(
                    kind="notes_phrase",
                    detail=f"Notes matched /{phrase}/",
                    strength="strong",
                    match_basis="notes_phrase",
                )
            )

        gmail_hits = payload.get("gmail_match_evidence") or []
        if isinstance(gmail_hits, list):
            for hit in gmail_hits:
                if not isinstance(hit, dict):
                    continue
                conf = hit.get("confidence", "matched")
                evidence.append(
                    LikelyAppliedEvidence(
                        kind="gmail_confirmation",
                        detail=(
                            f"Gmail msg {hit.get('message_id')} "
                            f"from {hit.get('sender_domain')} "
                            f"({conf})"
                        ),
                        strength="strong" if conf != "low" else "weak",
                        match_basis="gmail_company_and_role",
                    )
                )
                if conf == "low":
                    low = True

        if not evidence:
            continue
        has_strong = any(e.strength == "strong" for e in evidence)
        group = "strong" if has_strong else "weak"
        candidates.append(
            LikelyAppliedCandidate(
                record_id=row.id,
                company=row.company_name,
                role=row.role_title,
                job_url=row.job_url,
                status_mapped=row.status_mapped,
                applied_date_text=row.applied_date_text,
                evidence=evidence,
                low_confidence=low,
                strength=group,
                group=group,
            )
        )

    # Strong first, then weak; stable by company/role.
    candidates.sort(
        key=lambda c: (
            0 if c.group == "strong" else 1,
            c.company.lower(),
            c.role.lower(),
        )
    )
    return candidates


def list_likely_applied_grouped(session: Session) -> dict[str, Any]:
    items = list_likely_applied(session)
    strong = [c for c in items if c.group == "strong"]
    weak = [c for c in items if c.group == "weak"]
    return {
        "items": [c.to_dict() for c in items],
        "strong": [c.to_dict() for c in strong],
        "weak": [c.to_dict() for c in weak],
        "count": len(items),
        "strong_count": len(strong),
        "weak_count": len(weak),
        "low_confidence": sum(1 for c in items if c.low_confidence),
        "evidence_rules": {
            "applied_date": (
                "Weak only. Real Applied Date text; placeholders like N/A ignored. "
                "Simplify often fills Applied Date for SAVED rows."
            ),
            "matching_applied_record": (
                "Same canonical URL as an APPLIED SimplifyHistoryRecord, preferably "
                "with matching company. Import keeps distinct SAVED+APPLIED keys, so "
                "both rows can exist; this is not a precedence failure."
            ),
            "notes_phrase": f"Conservative regexes: {list(NOTES_APPLIED_PHRASES)}",
            "gmail_confirmation": "Opt-in gmail.readonly match on company+role",
        },
    }


def confirm_likely_applied(session: Session, record_ids: list[str]) -> dict[str, Any]:
    confirmed = 0
    missing = 0
    for rid in record_ids:
        row = session.query(SimplifyHistoryRecord).filter_by(id=rid).one_or_none()
        if row is None:
            missing += 1
            continue
        payload = dict(row.payload_json or {})
        payload[CONFIRM_FLAG] = True
        payload[REJECT_FLAG] = False
        payload["status_provenance"] = PROVENANCE_USER_CONFIRM
        payload["confirmed_at"] = datetime.now(UTC).isoformat()
        row.payload_json = payload
        flag_modified(row, "payload_json")
        row.status_mapped = "applied"
        session.add(row)
        session.add(
            AuditEvent(
                entity_type="simplify_history",
                entity_id=row.id,
                event_type="likely_applied_confirmed",
                payload={"provenance": PROVENANCE_USER_CONFIRM},
            )
        )
        confirmed += 1
    session.flush()
    return {"confirmed": confirmed, "missing": missing}


def reject_likely_applied(session: Session, record_ids: list[str]) -> dict[str, Any]:
    rejected = 0
    missing = 0
    for rid in record_ids:
        row = session.query(SimplifyHistoryRecord).filter_by(id=rid).one_or_none()
        if row is None:
            missing += 1
            continue
        payload = dict(row.payload_json or {})
        payload[REJECT_FLAG] = True
        payload["rejected_at"] = datetime.now(UTC).isoformat()
        row.payload_json = payload
        flag_modified(row, "payload_json")
        session.add(row)
        session.add(
            AuditEvent(
                entity_type="simplify_history",
                entity_id=row.id,
                event_type="likely_applied_rejected",
                payload={},
            )
        )
        rejected += 1
    session.flush()
    return {"rejected": rejected, "missing": missing}


def notes_phrase_rules() -> list[str]:
    return list(NOTES_APPLIED_PHRASES)
