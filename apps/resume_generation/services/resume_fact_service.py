from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from apps.resume_generation.schemas import ScanSummary
from apps.resume_generation.services.resume_corpus_service import (
    classify_document_track,
    discover_docx_files,
    extract_facts_from_docx,
    file_content_hash,
    normalize_text,
)
from packages.core.domain.models import ResumeFact, ResumeSourceDocument, make_uuid


@dataclass
class ConflictWarning:
    group: str
    message: str
    fact_ids: list[str] = field(default_factory=list)


def _mark_gpa_conflicts(session: Session) -> list[ConflictWarning]:
    """Same education institution must not expose multiple distinct GPAs."""
    warnings: list[ConflictWarning] = []
    edu = (
        session.query(ResumeFact)
        .filter(ResumeFact.section == "education", ResumeFact.gpa_value.isnot(None))
        .all()
    )
    by_inst: dict[str, list[ResumeFact]] = defaultdict(list)
    for fact in edu:
        # Prefer explicit parent; fall back to normalized institution-ish text
        key = normalize_text(fact.parent_name or "unknown_education")
        # Also fold bachelor/MS GPAs under institution when parent is degree line
        if "university" not in key and "college" not in key and "pes" not in key:
            # Try to use any institution-named sibling context via parent_key prefix
            key = normalize_text(fact.parent_key or key)
        by_inst[key].append(fact)

    # Also group all GPA values globally for undergrad/grad when institution unclear
    all_gpas = {f.gpa_value for f in edu if f.gpa_value}
    if len(all_gpas) > 1:
        group = "conflict:gpa:candidate_education"
        for fact in edu:
            fact.conflict_group = group
            fact.usable = False
            fact.verification_state = "conflict"
        warnings.append(
            ConflictWarning(
                group=group,
                message=(
                    "Conflicting GPA values across education records: "
                    + ", ".join(sorted(all_gpas))
                ),
                fact_ids=[f.id for f in edu],
            )
        )
        return warnings

    for key, facts in by_inst.items():
        gpas = {f.gpa_value for f in facts if f.gpa_value}
        if len(gpas) > 1:
            group = f"conflict:gpa:{key[:40]}"
            for fact in facts:
                fact.conflict_group = group
                fact.usable = False
                fact.verification_state = "conflict"
            warnings.append(
                ConflictWarning(
                    group=group,
                    message=f"Conflicting GPA values for {key}: {', '.join(sorted(gpas))}",
                    fact_ids=[f.id for f in facts],
                )
            )
    return warnings


def _mark_metric_conflicts(session: Session) -> list[ConflictWarning]:
    warnings: list[ConflictWarning] = []
    usable = (
        session.query(ResumeFact)
        .filter(
            ResumeFact.usable.is_(True),
            ResumeFact.section == "experience",
            ResumeFact.fact_kind == "bullet",
        )
        .all()
    )
    by_fp: dict[str, list[ResumeFact]] = defaultdict(list)
    for fact in usable:
        if not fact.metrics_json:
            continue
        base = "".join(ch for ch in fact.normalized_text if not ch.isdigit())
        base = " ".join(base.split())[:120]
        if len(base) > 20:
            by_fp[base].append(fact)
    for fp, facts in by_fp.items():
        metric_sets = {tuple(sorted(f.metrics_json or [])) for f in facts}
        if len(metric_sets) > 1:
            group = f"conflict:metrics:{hash(fp) & 0xFFFFFFFF:x}"
            for fact in facts:
                fact.conflict_group = group
                fact.usable = False
                fact.verification_state = "conflict"
            warnings.append(
                ConflictWarning(
                    group=group,
                    message="Conflicting metrics on near-duplicate bullets",
                    fact_ids=[f.id for f in facts],
                )
            )
    return warnings


def _mark_orphan_bullets(session: Session) -> list[ConflictWarning]:
    warnings: list[ConflictWarning] = []
    orphans = (
        session.query(ResumeFact)
        .filter(ResumeFact.fact_kind == "orphan_bullet", ResumeFact.usable.is_(True))
        .all()
    )
    if orphans:
        group = "conflict:orphan_bullets"
        for fact in orphans:
            fact.conflict_group = group
            fact.usable = False
            fact.verification_state = "needs_parent"
        warnings.append(
            ConflictWarning(
                group=group,
                message=f"{len(orphans)} bullets lack company/project parent context",
                fact_ids=[f.id for f in orphans],
            )
        )
    return warnings


def mark_all_conflicts(session: Session) -> list[ConflictWarning]:
    warnings: list[ConflictWarning] = []
    warnings.extend(_mark_gpa_conflicts(session))
    warnings.extend(_mark_metric_conflicts(session))
    warnings.extend(_mark_orphan_bullets(session))
    return warnings


def has_unresolved_identity_conflicts(session: Session) -> list[str]:
    """Return hard blockers. GPA conflicts are not hard-blocked when GPA is omitted."""
    return []


def gpa_conflict_warning(session: Session) -> str | None:
    """Warn when GPAs conflict; callers must omit GPA from generated output."""
    gpa_conflicts = (
        session.query(ResumeFact)
        .filter(
            ResumeFact.conflict_group.like("conflict:gpa:%"),
            ResumeFact.verification_state == "conflict",
        )
        .count()
    )
    if gpa_conflicts:
        return (
            "Unresolved GPA conflicts in corpus; GPA omitted from generated resume "
            "until an authoritative value is configured."
        )
    return None


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
    dry_run_gpas: set[str] = set()
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
            summary.changed += 1

            for fact in facts:
                seen_hashes[fact.content_hash] += 1
                if fact.gpa_value:
                    dry_run_gpas.add(fact.gpa_value)

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
                        fact_kind=fact.fact_kind,
                        source_text=fact.source_text,
                        normalized_text=fact.normalized_text,
                        content_hash=fact.content_hash,
                        employer_or_project=fact.employer_or_project,
                        parent_entity_type=fact.parent_entity_type,
                        parent_name=fact.parent_name,
                        role_title=fact.role_title,
                        parent_key=fact.parent_key,
                        gpa_value=fact.gpa_value,
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
    if dry_run or session is None:
        if len(dry_run_gpas) > 1:
            summary.conflict_warnings += 1
        return summary
    conflicts = mark_all_conflicts(session)
    summary.conflict_warnings = len(conflicts)
    hash_counts: dict[str, list[ResumeFact]] = defaultdict(list)
    for stored in session.query(ResumeFact).all():
        hash_counts[stored.content_hash].append(stored)
    for group in hash_counts.values():
        if len(group) >= 2:
            for stored in group:
                if stored.usable and stored.verification_state not in {
                    "conflict",
                    "needs_parent",
                }:
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
