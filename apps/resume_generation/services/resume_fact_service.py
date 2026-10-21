from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from apps.resume_generation.schemas import ScanSummary
from apps.resume_generation.services.resume_corpus_service import (
    discover_docx_files,
    extract_facts_from_docx,
    file_content_hash,
    classify_document_track,
)
from packages.core.domain.models import ResumeFact, ResumeSourceDocument, make_uuid


@dataclass
class ConflictWarning:
    group: str
    message: str
    fact_ids: list[str] = field(default_factory=list)


def _metric_fingerprint(text: str, metrics: list[str] | None) -> str | None:
    if not metrics:
        return None
    # Strip numbers so near-duplicate achievement lines can be compared
    base = "".join(ch for ch in text.lower() if not ch.isdigit())
    base = " ".join(base.split())
    return base[:120] if len(base) > 20 else None


def _mark_conflicts(session: Session) -> list[ConflictWarning]:
    """Flag near-duplicate bullets that disagree on metrics or dates."""
    warnings: list[ConflictWarning] = []
    usable = (
        session.query(ResumeFact)
        .filter(ResumeFact.usable.is_(True), ResumeFact.section == "experience")
        .all()
    )
    by_fingerprint: dict[str, list[ResumeFact]] = defaultdict(list)
    for fact in usable:
        fp = _metric_fingerprint(fact.normalized_text, fact.metrics_json)
        if fp:
            by_fingerprint[fp].append(fact)

    for fp, facts in by_fingerprint.items():
        metric_sets = {tuple(sorted(f.metrics_json or [])) for f in facts}
        date_sets = {f.dates_text for f in facts if f.dates_text}
        if len(metric_sets) > 1 or len(date_sets) > 1:
            group = f"conflict:{hash(fp) & 0xFFFFFFFF:x}"
            for fact in facts:
                fact.conflict_group = group
                fact.usable = False
                fact.verification_state = "conflict"
            warnings.append(
                ConflictWarning(
                    group=group,
                    message="Conflicting metrics/dates on near-duplicate bullets",
                    fact_ids=[f.id for f in facts],
                )
            )
    return warnings


def scan_resume_corpus(
    session: Session | None,
    *,
    path: Path,
    dry_run: bool = False,
    rebuild: bool = False,
) -> ScanSummary:
    files, summary = discover_docx_files(path)
    if rebuild and not dry_run:
        if session is None:
            raise ValueError("session required unless --dry-run")
        session.query(ResumeFact).delete()
        session.query(ResumeSourceDocument).delete()
        session.flush()

    seen_hashes: dict[str, int] = defaultdict(int)
    for docx_path in files:
        try:
            content_hash = file_content_hash(docx_path)
            existing = None
            if session is not None and not dry_run:
                existing = (
                    session.query(ResumeSourceDocument)
                    .filter(
                        ResumeSourceDocument.absolute_path == str(docx_path.resolve())
                    )
                    .one_or_none()
                )
                if existing and existing.content_hash == content_hash and not rebuild:
                    summary.unchanged += 1
                    continue

            facts = extract_facts_from_docx(docx_path, path)
            track = classify_document_track(docx_path, path)
            summary.parsed += 1
            if existing and existing.content_hash != content_hash:
                summary.changed += 1
            else:
                summary.changed += 1

            for fact in facts:
                seen_hashes[fact.content_hash] += 1

            if dry_run or session is None:
                continue

            if existing:
                session.query(ResumeFact).filter(
                    ResumeFact.source_document_id == existing.id
                ).delete()
                existing.content_hash = content_hash
                existing.track = track
                existing.filename = docx_path.name
                existing.fact_count = len(facts)
                existing.parse_status = "ok"
                existing.last_scanned_at = datetime.now(UTC)
                doc = existing
            else:
                doc = ResumeSourceDocument(
                    id=make_uuid(),
                    absolute_path=str(docx_path.resolve()),
                    filename=docx_path.name,
                    track=track,
                    content_hash=content_hash,
                    parse_status="ok",
                    fact_count=len(facts),
                    last_scanned_at=datetime.now(UTC),
                )
                session.add(doc)
                session.flush()

            for fact in facts:
                session.add(
                    ResumeFact(
                        id=make_uuid(),
                        source_document_id=doc.id,
                        section=fact.section,
                        source_text=fact.source_text,
                        normalized_text=fact.normalized_text,
                        content_hash=fact.content_hash,
                        employer_or_project=fact.employer_or_project,
                        skills_json=fact.skills or None,
                        dates_text=fact.dates_text,
                        metrics_json=fact.metrics or None,
                        verification_state="source_extracted",
                        usable=True,
                    )
                )
        except Exception as exc:
            summary.errors.append(f"{docx_path.name}: {exc.__class__.__name__}")

    summary.duplicate_facts = sum(1 for n in seen_hashes.values() if n > 1)
    if not dry_run and session is not None:
        conflicts = _mark_conflicts(session)
        summary.conflict_warnings = len(conflicts)
        # Corroborate duplicates across sources
        hash_counts: dict[str, list[ResumeFact]] = defaultdict(list)
        for stored in session.query(ResumeFact).all():
            hash_counts[stored.content_hash].append(stored)
        for group in hash_counts.values():
            if len(group) >= 2:
                for stored in group:
                    if stored.usable and stored.verification_state != "conflict":
                        stored.verification_state = "corroborated"
        session.flush()
    return summary


def usable_facts(session: Session) -> list[ResumeFact]:
    return (
        session.query(ResumeFact)
        .filter(ResumeFact.usable.is_(True))
        .order_by(ResumeFact.section.asc(), ResumeFact.created_at.asc())
        .all()
    )
