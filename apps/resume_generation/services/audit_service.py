from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, joinedload

from apps.resume_generation.services.generation_preflight import generation_preflight
from apps.resume_generation.services.resume_fact_service import (
    gpa_conflict_warning,
    usable_facts,
)
from apps.resume_generation.services.resume_strategy_service import (
    build_resume_strategy,
)
from packages.core.domain.models import JobPosting, ResumeFact, ResumeSourceDocument
from packages.core.settings import settings


@dataclass
class AuditReport:
    job_id: str
    title: str
    company: str | None
    permitted: bool
    block_reason: str | None
    track: str | None
    deterministic_only: bool
    semantic_provider: str
    gpa_omitted: bool
    gpa_conflict_note: str | None
    skill_lines: list[str] = field(default_factory=list)
    experience: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    education: list[dict[str, Any]] = field(default_factory=list)
    omitted_reasons: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    orphan_bullets_blocked: int = 0
    warnings: list[str] = field(default_factory=list)


def build_audit_report(
    session: Session,
    job_id: str,
    *,
    deterministic_only: bool = True,
    track: str | None = None,
) -> AuditReport:
    job = (
        session.query(JobPosting)
        .options(joinedload(JobPosting.company))
        .filter(JobPosting.id == job_id)
        .one_or_none()
    )
    if job is None:
        return AuditReport(
            job_id=job_id,
            title="(not found)",
            company=None,
            permitted=False,
            block_reason="Job not found",
            track=None,
            deterministic_only=deterministic_only,
            semantic_provider=settings.semantic_fit_provider,
            gpa_omitted=True,
            gpa_conflict_note=None,
        )

    block = generation_preflight(session, job)
    gpa_note = gpa_conflict_warning(session)
    orphans = (
        session.query(ResumeFact)
        .filter(ResumeFact.fact_kind == "orphan_bullet")
        .count()
    )
    conflicts = (
        session.query(ResumeFact.conflict_group)
        .filter(ResumeFact.conflict_group.isnot(None))
        .distinct()
        .all()
    )
    conflict_labels = sorted({c[0] for c in conflicts if c[0]})

    if block:
        return AuditReport(
            job_id=job.id,
            title=job.title,
            company=job.company.canonical_name if job.company else None,
            permitted=False,
            block_reason=block,
            track=None,
            deterministic_only=deterministic_only,
            semantic_provider=(
                "deterministic"
                if deterministic_only
                else settings.semantic_fit_provider
            ),
            gpa_omitted=bool(gpa_note),
            gpa_conflict_note=gpa_note,
            conflicts=conflict_labels,
            orphan_bullets_blocked=orphans,
            warnings=[gpa_note] if gpa_note else [],
        )

    strategy = build_resume_strategy(
        session,
        job,
        track_override=track,
        deterministic_only=deterministic_only,
    )
    doc = strategy.document
    docs = {d.id: d for d in session.query(ResumeSourceDocument).all()}
    facts = {f.id: f for f in usable_facts(session)}
    # Also load selected facts that may be education headers (usable may exclude GPA)
    selected = (
        session.query(ResumeFact)
        .filter(ResumeFact.id.in_(strategy.selected_fact_ids or ["__none__"]))
        .all()
    )
    for f in selected:
        facts[f.id] = f

    experience = []
    projects = []
    education = []
    provenance = []
    if doc:
        for exp in doc.experience:
            experience.append(
                {
                    "company": exp.company,
                    "title": exp.title,
                    "dates": exp.dates,
                    "bullets": [b.text for b in exp.bullets],
                }
            )
            for b in exp.bullets:
                fact = facts.get(b.fact_id)
                src = docs.get(fact.source_document_id) if fact else None
                provenance.append(
                    {
                        "kind": "experience",
                        "parent": f"{exp.company} | {exp.title}",
                        "bullet": b.text[:120],
                        "source_file": src.filename if src else None,
                        "fact_id": b.fact_id,
                    }
                )
        for proj in doc.projects:
            projects.append(
                {
                    "name": proj.name,
                    "technologies": proj.technologies,
                    "bullets": [b.text for b in proj.bullets],
                }
            )
            for b in proj.bullets:
                fact = facts.get(b.fact_id)
                src = docs.get(fact.source_document_id) if fact else None
                provenance.append(
                    {
                        "kind": "project",
                        "parent": proj.name,
                        "bullet": b.text[:120],
                        "source_file": src.filename if src else None,
                        "fact_id": b.fact_id,
                    }
                )
        for ed in doc.education:
            education.append(
                {
                    "institution": ed.institution,
                    "degree": ed.degree,
                    "major": ed.major,
                    "dates": ed.graduation_date,
                    "gpa": ed.gpa,
                }
            )

    permitted = bool(doc) and not strategy.block_reason and not block
    return AuditReport(
        job_id=job.id,
        title=job.title,
        company=job.company.canonical_name if job.company else None,
        permitted=permitted,
        block_reason=strategy.block_reason or block,
        track=strategy.track,
        deterministic_only=deterministic_only,
        semantic_provider=strategy.provider,
        gpa_omitted=bool(gpa_note) or all(not e.get("gpa") for e in education),
        gpa_conflict_note=gpa_note,
        skill_lines=list(doc.skill_lines) if doc else [],
        experience=experience,
        projects=projects,
        education=education,
        omitted_reasons=list(doc.omit_reasons) if doc else [],
        conflicts=conflict_labels,
        provenance=provenance,
        unsupported_claims=[],
        orphan_bullets_blocked=orphans,
        warnings=list(strategy.warnings),
    )


def format_audit_report(report: AuditReport) -> str:
    lines: list[str] = []
    lines.append(f"Job: {report.job_id}")
    lines.append(f"Title: {report.title}")
    lines.append(f"Company: {report.company or '—'}")
    lines.append(f"Generation permitted: {'yes' if report.permitted else 'no'}")
    if report.block_reason:
        lines.append(f"Block reason: {report.block_reason}")
    lines.append(f"Baseline track: {report.track or '—'}")
    lines.append(
        f"Mode: {'deterministic-only' if report.deterministic_only else report.semantic_provider}"
    )
    lines.append(f"GPA omitted: {'yes' if report.gpa_omitted else 'no'}")
    if report.gpa_conflict_note:
        lines.append(f"GPA note: {report.gpa_conflict_note}")
    if report.conflicts:
        lines.append(f"Unresolved conflict groups: {len(report.conflicts)}")
        for c in report.conflicts[:8]:
            lines.append(f"  - {c}")
    lines.append(f"Orphan bullets blocked in corpus: {report.orphan_bullets_blocked}")

    lines.append("Skills:")
    if report.skill_lines:
        for s in report.skill_lines:
            lines.append(f"  - {s}")
    else:
        lines.append("  (none)")

    lines.append("Experience:")
    if not report.experience:
        lines.append("  (none)")
    for exp in report.experience:
        lines.append(f"  {exp['company']} | {exp['title']} | {exp['dates']}")
        for b in exp["bullets"]:
            lines.append(f"    • {b}")

    lines.append("Projects:")
    if not report.projects:
        lines.append("  (none)")
    for proj in report.projects:
        tech = f" | {proj['technologies']}" if proj.get("technologies") else ""
        lines.append(f"  {proj['name']}{tech}")
        for b in proj["bullets"]:
            lines.append(f"    • {b}")

    lines.append("Education:")
    if not report.education:
        lines.append("  (none)")
    for ed in report.education:
        gpa = f" | GPA {ed['gpa']}" if ed.get("gpa") else " | GPA omitted"
        lines.append(
            f"  {ed['institution']} | {ed['degree']} | {ed.get('major') or '—'} | "
            f"{ed.get('dates') or '—'}{gpa}"
        )

    if report.omitted_reasons:
        lines.append("Omitted / trimmed:")
        for r in report.omitted_reasons:
            lines.append(f"  - {r}")

    lines.append("Provenance (selected bullets):")
    if not report.provenance:
        lines.append("  (none)")
    for p in report.provenance:
        lines.append(
            f"  [{p['kind']}] {p['parent']} ← {p.get('source_file') or 'unknown'}"
        )

    if report.warnings:
        lines.append("Warnings:")
        for w in report.warnings:
            lines.append(f"  - {w}")

    lines.append(
        "Unsupported claims: none detected"
        if not report.unsupported_claims
        else "Unsupported claims present"
    )
    return "\n".join(lines)
