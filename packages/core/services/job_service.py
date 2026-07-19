from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from packages.core.domain.models import (
    JobPosting,
    Company,
    JobAssessment,
    ReferralTask,
    AuditEvent,
    make_description_hash,
    make_dedupe_key,
)


@dataclass
class ImportResult:
    created: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


def _get_or_create_company(session: Session, name: str) -> Company | None:
    name_norm = (name or "").strip()
    if not name_norm:
        return None
    existing = session.query(Company).filter(Company.canonical_name == name_norm).one_or_none()
    if existing:
        return existing
    c = Company(canonical_name=name_norm)
    session.add(c)
    session.flush()
    return c


def _parse_row(row: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    # Validate required fields
    title = (row.get("title") or "").strip()
    company = (row.get("company") or "").strip()
    description_text = (row.get("description_text") or "").strip()
    if not title or not company or not description_text:
        return None, "missing required fields: title/company/description_text"
    return {
        "title": title,
        "company": company,
        "description_text": description_text,
        "canonical_url": row.get("url") or row.get("canonical_url"),
        "external_id": row.get("external_id"),
        "location": row.get("location"),
        "workplace_type": row.get("workplace_type"),
        "employment_type": row.get("employment_type"),
        "posted_at": row.get("posted_at"),
        "application_deadline": row.get("application_deadline"),
        "raw_payload": row,
    }, None


def import_csv(session: Session, path: str) -> ImportResult:
    res = ImportResult(created=0, duplicates=0, errors=[])
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            data, err = _parse_row(row)
            if err:
                res.errors.append(err)
                continue
            assert data is not None
            created = _create_or_flag(session, data, source="csv_import")
            if created:
                res.created += 1
            else:
                res.duplicates += 1
    return res


def import_json(session: Session, path: str) -> ImportResult:
    res = ImportResult(created=0, duplicates=0, errors=[])
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
        if isinstance(data, dict):
            rows = [data]
        else:
            rows = data
        for row in rows:
            parsed, err = _parse_row(row)
            if err:
                res.errors.append(err)
                continue
            assert parsed is not None
            created = _create_or_flag(session, parsed, source="json_import")
            if created:
                res.created += 1
            else:
                res.duplicates += 1
    return res


def _create_or_flag(session: Session, data: dict[str, Any], source: str) -> bool:
    title = data["title"]
    company_name = data["company"]
    description_text = data["description_text"]

    desc_hash = make_description_hash(description_text)
    dedupe_key = make_dedupe_key(company_name, title, data.get("location"))

    # Check for suspected duplicates by dedupe_key or description_hash
    existing = (
        session.query(JobPosting)
        .filter((JobPosting.dedupe_key == dedupe_key) | (JobPosting.description_hash == desc_hash))
        .one_or_none()
    )

    if existing:
        # Create audit event and mark existing for review
        ev = AuditEvent(entity_type="job_posting", entity_id=existing.id, event_type="duplicate_suspected", payload={"source": source})
        session.add(ev)
        setattr(existing, "status", "queued_for_review")
        session.add(existing)
        session.flush()
        return False

    company = _get_or_create_company(session, company_name)

    jp = JobPosting(
        source=source,
        external_id=data.get("external_id"),
        canonical_url=data.get("canonical_url"),
        title=title,
        company_id=company.id if company else None,
        location=data.get("location"),
        workplace_type=data.get("workplace_type"),
        employment_type=data.get("employment_type"),
        description_text=description_text,
        posted_at=_parse_date(data.get("posted_at")),
        application_deadline=_parse_date(data.get("application_deadline")),
        status="new",
        normalized_title=title.lower().strip(),
        description_hash=desc_hash,
        dedupe_key=dedupe_key,
        raw_payload=data.get("raw_payload") if source != "manual" else None,
    )
    session.add(jp)
    session.flush()

    ev = AuditEvent(entity_type="job_posting", entity_id=jp.id, event_type="ingested", payload={"source": source})
    session.add(ev)
    session.flush()
    return True


def _parse_date(val: Any) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except Exception:
        return None


# Scoring logic

TRACK_KEYWORDS = {
    "ml": [
        "machine learning",
        "machine-learning",
        "mlops",
        "deep learning",
        "tensorflow",
        "pytorch",
        "scikit",
        "data scientist",
        "model",
        "nlp",
    ],
    "cloud": [
        "aws",
        "azure",
        "gcp",
        "kubernetes",
        "docker",
        "terraform",
        "cloud",
        "devops",
        "infrastructure",
    ],
    "dev": [
        "backend",
        "api",
        "python",
        "java",
        "node",
        "software engineer",
        "full-stack",
        "frontend",
    ],
}


def assess_job(session: Session, job_id: str) -> JobAssessment:
    jp = session.query(JobPosting).filter(JobPosting.id == job_id).one()
    text = (jp.title or "") + "\n" + (jp.description_text or "")
    text_lower = text.lower()

    # Short description -> manual review
    if len(jp.description_text or "") < 100 or len((jp.title or "").split()) < 2:
        rec = "manual_review"
        score = 0
        explanation = {"reason": "insufficient_text"}
        assessment = JobAssessment(
            job_posting_id=jp.id,
            recommended_track=rec,
            score=score,
            score_explanation=explanation,
            key_skills=[],
            missing_or_uncertain_skills=[],
            manual_review_reasons=["short_description_or_ambiguous_title"],
        )
        session.add(assessment)
        session.flush()
        ev = AuditEvent(entity_type="job_posting", entity_id=jp.id, event_type="assessed", payload={"track": rec})
        session.add(ev)
        return assessment

    # Score per track
    track_scores = {}
    track_matches = {}
    for track, kws in TRACK_KEYWORDS.items():
        matches = [k for k in kws if k in text_lower]
        track_matches[track] = matches
        # base score on number of matches and presence in title
        score = len(matches) * 20
        for m in matches:
            if m in (jp.title or "").lower():
                score += 10
        track_scores[track] = min(100, score)

    # Choose best track
    best = max(track_scores.items(), key=lambda kv: kv[1])
    rec_track = best[0]
    score = best[1]

    # If best score is low, choose manual_review
    if score < 30:
        rec_track = "manual_review"

    assessment = JobAssessment(
        job_posting_id=jp.id,
        recommended_track=rec_track,
        score=score,
        score_explanation={"scores": track_scores, "matches": track_matches},
        key_skills=track_matches.get(rec_track, []),
        missing_or_uncertain_skills=[],
        manual_review_reasons=[],
    )
    session.add(assessment)
    setattr(jp, "status", "queued_for_review")
    ev = AuditEvent(entity_type="job_posting", entity_id=jp.id, event_type="assessed", payload={"track": rec_track, "score": score})
    session.add(ev)
    session.flush()
    return assessment


def list_review_queue(session: Session) -> list[JobPosting]:
    return session.query(JobPosting).filter(JobPosting.status.in_(["queued_for_review", "new"])).all()


def start_referral(session: Session, job_id: str, cutoff_hours: int = 48) -> ReferralTask:
    jp = session.query(JobPosting).filter(JobPosting.id == job_id).one()
    rt = ReferralTask(job_posting_id=jp.id, status="draft_ready", cutoff_at=datetime.utcnow() + timedelta(hours=cutoff_hours))
    session.add(rt)
    ev = AuditEvent(entity_type="job_posting", entity_id=jp.id, event_type="referral_started", payload={"cutoff_hours": cutoff_hours})
    session.add(ev)
    session.flush()
    return rt
