from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from apps.resume_generation.schemas import (
    BulletRef,
    EducationEntry,
    ExperienceEntry,
    ProjectEntry,
    ResumeDocument,
)
from apps.resume_generation.services.resume_corpus_service import (
    normalize_skill_token,
    normalize_text,
)
from packages.core.domain.models import ResumeFact, ResumeSourceDocument


def _near_dupe_key(text: str) -> str:
    """Collapse near-duplicate bullets that share the same claim skeleton."""
    base = "".join(ch for ch in text.lower() if not ch.isdigit())
    base = " ".join(base.split())
    words = [w for w in base.split() if len(w) > 2][:12]
    return " ".join(words)


def _is_near_duplicate(
    candidate: str, existing: list[str], threshold: float = 0.62
) -> bool:
    cand = _near_dupe_key(candidate)
    for prev in existing:
        ratio = SequenceMatcher(None, cand, _near_dupe_key(prev)).ratio()
        if ratio >= threshold:
            return True
        # Shared multi-cloud / infra claim skeleton
        shared = set(cand.split()) & set(_near_dupe_key(prev).split())
        heavy = {
            "aws",
            "gcp",
            "azure",
            "terraform",
            "kubernetes",
            "jenkins",
            "kafka",
            "ansible",
            "mern",
            "axios",
        }
        if len(shared & heavy) >= 3 and ratio >= 0.5:
            return True
    return False


def _role_sort_key(entry: ExperienceEntry) -> tuple[int, int]:
    """Reverse-chronological: prefer Present / later end years."""
    dates = (entry.dates or "").lower()
    present = 1 if "present" in dates else 0
    years = [int(y) for y in re.findall(r"(20\d{2})", entry.dates or "")]
    end = max(years) if years else 0
    return (present, end)


SKILL_CATEGORIES: list[tuple[str, set[str]]] = [
    (
        "Languages",
        {
            "python",
            "java",
            "javascript",
            "typescript",
            "c++",
            "c#",
            "go",
            "sql",
            "shell",
            "bash",
        },
    ),
    (
        "Cloud & DevOps",
        {
            "aws",
            "gcp",
            "azure",
            "kubernetes",
            "docker",
            "terraform",
            "jenkins",
            "linux",
            "ansible",
            "grafana",
        },
    ),
    (
        "Data & ML",
        {"pytorch", "tensorflow", "kafka", "mongodb", "postgresql", "influxdb"},
    ),
]


def _display_skill(token: str) -> str:
    special = {
        "c++": "C++",
        "c#": "C#",
        "aws": "AWS",
        "gcp": "GCP",
        "sql": "SQL",
        "pytorch": "PyTorch",
    }
    if token in special:
        return special[token]
    return token.capitalize() if token.islower() else token


def build_deduped_skill_lines(facts: list[ResumeFact]) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for fact in facts:
        if fact.section != "skills" or not fact.usable:
            continue
        for raw in fact.skills_json or []:
            tok = normalize_skill_token(raw)
            if tok and tok not in seen:
                seen.add(tok)
                tokens.append(tok)
    lines: list[str] = []
    used: set[str] = set()
    for label, members in SKILL_CATEGORIES:
        chosen = [t for t in tokens if t in members]
        if chosen:
            lines.append(f"{label}: {', '.join(_display_skill(t) for t in chosen)}")
            used.update(chosen)
    leftover = [t for t in tokens if t not in used]
    if leftover:
        lines.append(f"Other: {', '.join(_display_skill(t) for t in leftover)}")
    return lines


def _build_experience_entries(
    facts: list[ResumeFact],
    *,
    keywords: list[str],
    limit_roles: int = 3,
    bullets_per_role: int = 2,
) -> list[ExperienceEntry]:
    headers = [
        f
        for f in facts
        if f.section == "experience"
        and f.fact_kind == "role_header"
        and f.usable
        and f.parent_key
    ]
    bullets = [
        f
        for f in facts
        if f.section == "experience"
        and f.fact_kind == "bullet"
        and f.usable
        and f.parent_key
    ]
    by_key: dict[str, list[ResumeFact]] = defaultdict(list)
    for b in bullets:
        by_key[b.parent_key or ""].append(b)

    # Prefer unique parent_keys from headers; fall back to bullet keys
    keys: list[str] = []
    seen_keys: set[str] = set()
    header_by_key: dict[str, ResumeFact] = {}
    for h in headers:
        key = h.parent_key or ""
        if key and key not in seen_keys:
            seen_keys.add(key)
            keys.append(key)
            header_by_key[key] = h
    for key in by_key:
        if key and key not in seen_keys:
            seen_keys.add(key)
            keys.append(key)

    def score_role(key: str) -> int:
        score = 0
        for b in by_key.get(key, []):
            text = b.normalized_text
            score += sum(2 for kw in keywords if kw in text)
            if b.verification_state == "corroborated":
                score += 1
        return score

    keys.sort(key=score_role, reverse=True)
    entries: list[ExperienceEntry] = []
    used_bullet_norms: set[str] = set()

    for key in keys:
        if len(entries) >= limit_roles:
            break
        header = header_by_key.get(key)
        company = (header.parent_name if header else None) or (
            by_key[key][0].parent_name if by_key.get(key) else None
        )
        title = (header.role_title if header else None) or (
            by_key[key][0].role_title if by_key.get(key) else None
        )
        dates = (header.dates_text if header else None) or (
            by_key[key][0].dates_text if by_key.get(key) else None
        )
        if not company or not title:
            continue
        # Avoid identical company+title when title was copied from company
        if normalize_text(company) == normalize_text(title) and header:
            # Try to recover a better title from header source text
            title = header.source_text.split("|")[0].strip()[:80] or title

        scored_bullets = sorted(
            by_key.get(key, []),
            key=lambda f: (
                -sum(2 for kw in keywords if kw in f.normalized_text),
                0 if f.verification_state == "corroborated" else 1,
                f.source_text,
            ),
        )
        chosen: list[BulletRef] = []
        chosen_texts: list[str] = []
        for b in scored_bullets:
            if b.normalized_text in used_bullet_norms:
                continue
            if _is_near_duplicate(
                b.normalized_text, chosen_texts + list(used_bullet_norms)
            ):
                continue
            used_bullet_norms.add(b.normalized_text)
            chosen_texts.append(b.normalized_text)
            chosen.append(
                BulletRef(
                    text=b.source_text, fact_id=b.id, normalized=b.normalized_text
                )
            )
            if len(chosen) >= bullets_per_role:
                break
        if not chosen:
            continue
        # Prefer distinct company vs title; if identical, try to split "Title at Company"
        display_company = company
        display_title = title
        if normalize_text(company) == normalize_text(title):
            # Incomplete header — skip rather than render anonymous/duplicated role
            if " at " in company.lower():
                left, _, right = company.partition(" at ")
                display_title, display_company = left.strip(), right.strip()
            else:
                continue
        entries.append(
            ExperienceEntry(
                company=display_company,
                title=display_title,
                dates=dates or "",
                bullets=chosen,
                parent_key=key,
            )
        )
    entries.sort(key=_role_sort_key, reverse=True)
    return entries


def _build_project_entries(
    facts: list[ResumeFact],
    *,
    keywords: list[str],
    limit_projects: int = 2,
    bullets_per: int = 2,
) -> list[ProjectEntry]:
    headers = [
        f
        for f in facts
        if f.section == "projects"
        and f.fact_kind == "project_header"
        and f.usable
        and f.parent_key
    ]
    bullets = [
        f
        for f in facts
        if f.section == "projects"
        and f.fact_kind == "bullet"
        and f.usable
        and f.parent_key
    ]
    by_key: dict[str, list[ResumeFact]] = defaultdict(list)
    for b in bullets:
        by_key[b.parent_key or ""].append(b)
    header_by_key = {h.parent_key: h for h in headers if h.parent_key}
    keys = list(dict.fromkeys([*(header_by_key.keys()), *by_key.keys()]))

    def score(key: str) -> int:
        return sum(
            sum(2 for kw in keywords if kw in b.normalized_text)
            for b in by_key.get(key, [])
        )

    keys.sort(key=score, reverse=True)
    entries: list[ProjectEntry] = []
    used_norms: set[str] = set()
    for key in keys:
        if len(entries) >= limit_projects:
            break
        header = header_by_key.get(key)
        name = (header.parent_name if header else None) or (
            by_key[key][0].parent_name if by_key.get(key) else None
        )
        if not name:
            continue
        techs = None
        if header and header.skills_json:
            techs = ", ".join(
                sorted({normalize_skill_token(s) for s in header.skills_json})
            )
        chosen: list[BulletRef] = []
        chosen_texts: list[str] = []
        for b in sorted(
            by_key.get(key, []),
            key=lambda f: -sum(2 for kw in keywords if kw in f.normalized_text),
        ):
            if b.normalized_text in used_norms:
                continue
            if _is_near_duplicate(b.normalized_text, chosen_texts + list(used_norms)):
                continue
            used_norms.add(b.normalized_text)
            chosen_texts.append(b.normalized_text)
            chosen.append(
                BulletRef(
                    text=b.source_text, fact_id=b.id, normalized=b.normalized_text
                )
            )
            if len(chosen) >= bullets_per:
                break
        if not chosen:
            continue
        entries.append(
            ProjectEntry(
                name=name,
                bullets=chosen,
                technologies=techs,
                date_range=header.dates_text if header else None,
                parent_key=key,
            )
        )
    return entries


def _clean_edu_text(text: str) -> str:
    import re

    text = re.sub(
        r"(?:GPA|G\.P\.A\.?)[:\s]*[0-9]\.[0-9]{1,2}\s*(?:/\s*4(?:\.0)?)?",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"^major in\s+", "", text, flags=re.I)
    return " ".join(text.split()).strip(" |-")


def _build_education_entries(facts: list[ResumeFact]) -> list[EducationEntry]:
    """One entry per distinct institution+degree; GPA omitted if conflicts exist."""
    all_edu = [f for f in facts if f.section == "education"]
    conflicted_gpa = len({f.gpa_value for f in all_edu if f.gpa_value}) > 1 or any(
        f.verification_state == "conflict" and f.gpa_value for f in all_edu
    )

    # Seed only from education headers with identifiable school + degree
    institutions: dict[str, EducationEntry] = {}
    for fact in all_edu:
        if fact.fact_kind != "education_header":
            continue
        text = fact.source_text
        lower = text.lower()
        inst = fact.parent_name or ""
        degree = fact.role_title or ""
        if "pennsylvania state" in lower or "penn state" in (inst or "").lower():
            inst = "The Pennsylvania State University"
        elif "pes university" in lower or "pes university" in (inst or "").lower():
            inst = "PES University"
        if "master" in lower or "master" in (degree or "").lower():
            degree = "Master of Science"
        elif any(
            tok in lower or tok in (degree or "").lower()
            for tok in ("bachelor", "b.tech", "btech")
        ):
            degree = "Bachelor of Technology"
        if not inst or not degree:
            continue
        if "major" in normalize_text(inst) or "gpa" in normalize_text(inst):
            continue
        key = normalize_text(f"{inst}|{degree}")
        if key not in institutions:
            institutions[key] = EducationEntry(
                institution=_clean_edu_text(inst),
                degree=degree,
                graduation_date=fact.dates_text,
                major="Computer Science and Engineering",
                parent_key=key,
                fact_ids=[fact.id],
            )
        else:
            institutions[key].fact_ids.append(fact.id)
            if fact.dates_text and not institutions[key].graduation_date:
                institutions[key].graduation_date = fact.dates_text

    from packages.core.settings import settings

    resolved_penn = (settings.authoritative_penn_state_gpa or "").strip() or None
    resolved_pes = (settings.authoritative_pes_gpa or "").strip() or None
    for entry in institutions.values():
        inst = entry.institution.lower()
        if resolved_penn and "pennsylvania state" in inst:
            entry.gpa = resolved_penn
        elif resolved_pes and inst.startswith("pes "):
            entry.gpa = resolved_pes
        elif conflicted_gpa:
            entry.gpa = None

    ordered = sorted(
        institutions.values(),
        key=lambda e: (0 if "master" in e.degree.lower() else 1, e.institution),
    )
    return ordered[:2]


def validate_document_structure(doc: ResumeDocument) -> list[str]:
    errors: list[str] = []
    if len(doc.skill_lines) == 0:
        errors.append("Resume plan missing Skills section content")
    # Duplicate skills across lines
    all_skills: list[str] = []
    for line in doc.skill_lines:
        if ":" in line:
            parts = line.split(":", 1)[1]
            for tok in parts.split(","):
                all_skills.append(normalize_text(tok))
    if len(all_skills) != len(set(all_skills)):
        errors.append("Duplicate normalized skills in plan")
    for exp in doc.experience:
        if not exp.company or not exp.title:
            errors.append("Experience entry missing company or title")
        for b in exp.bullets:
            if not exp.company:
                errors.append("Experience bullet without company parent")
    for proj in doc.projects:
        if not proj.name:
            errors.append("Project entry missing name")
    if not doc.education:
        errors.append("Resume plan missing Education")
    if len(doc.education) > 2:
        errors.append("Too many education entries in plan")
    # Duplicate bullets
    norms = [b.normalized for e in doc.experience for b in e.bullets]
    norms += [b.normalized for e in doc.projects for b in e.bullets]
    if len(norms) != len(set(norms)):
        errors.append("Duplicate normalized bullets in plan")
    orphan_check = any(not e.company or not e.title for e in doc.experience)
    if orphan_check:
        errors.append("Experience entry incomplete")
    return errors


def build_resume_document(
    session: Session,
    *,
    track: str,
    keywords: list[str],
    prefer_baseline_docs: bool = True,
) -> ResumeDocument:
    docs = {d.id: d for d in session.query(ResumeSourceDocument).all()}
    company_specific_ids = {
        d.id for d in docs.values() if d.track == "company_specific_reference"
    }
    raw_facts = session.query(ResumeFact).filter(ResumeFact.usable.is_(True)).all()
    # Company-specific resumes are supporting evidence only when corroborated
    facts = [
        f
        for f in raw_facts
        if f.source_document_id not in company_specific_ids
        or f.verification_state == "corroborated"
    ]
    # Prefer baseline track sources for headers/skills when available
    if prefer_baseline_docs:
        baseline_ids = {
            d.id
            for d in docs.values()
            if d.track == track and d.track != "company_specific_reference"
        }
        if baseline_ids:
            baseline_facts = [f for f in facts if f.source_document_id in baseline_ids]
            skill_source = baseline_facts or facts
        else:
            skill_source = facts
    else:
        skill_source = facts

    # Exclude company-specific-only unverified for skills already handled by usable flag
    contact = None
    for fact in facts:
        if (
            fact.section == "other"
            and "@" in fact.source_text
            and len(fact.source_text) < 160
        ):
            contact = fact.source_text.replace("•", "|")
            break

    skill_lines = build_deduped_skill_lines(skill_source)
    experience = _build_experience_entries(facts, keywords=keywords)
    projects = _build_project_entries(facts, keywords=keywords)
    education = _build_education_entries(
        session.query(ResumeFact).filter(ResumeFact.section == "education").all()
    )

    fact_ids: list[str] = []
    for e in experience:
        fact_ids.extend(b.fact_id for b in e.bullets)
    for p in projects:
        fact_ids.extend(b.fact_id for b in p.bullets)
    for ed in education:
        fact_ids.extend(ed.fact_ids)

    source_files = sorted(
        {
            docs[f.source_document_id].filename
            for f in facts
            if f.id in set(fact_ids) and f.source_document_id in docs
        }
    )
    # Also credit skill source docs
    for f in skill_source:
        if f.section == "skills" and f.source_document_id in docs:
            source_files.append(docs[f.source_document_id].filename)
    source_files = sorted(set(source_files))

    return ResumeDocument(
        name_line="Alex Michael Manjakunnel",
        contact_line=contact,
        summary=None,
        skill_lines=skill_lines,
        experience=experience,
        projects=projects,
        education=education,
        fact_ids=fact_ids,
        source_files=source_files,
        track=track,
    )


def trim_document(doc: ResumeDocument, *, pass_number: int) -> ResumeDocument:
    reasons = list(doc.omit_reasons)
    experience = list(doc.experience)
    projects = list(doc.projects)
    if pass_number == 1:
        if doc.summary:
            reasons.append("Dropped summary to fit one page.")
            doc.summary = None
        if len(projects) > 1:
            reasons.append("Trimmed projects for page budget.")
            projects = projects[:1]
        for e in experience:
            if len(e.bullets) > 2:
                reasons.append(f"Trimmed bullets under {e.company} for page budget.")
                e.bullets = e.bullets[:2]
    elif pass_number >= 2:
        if projects:
            reasons.append("Dropped projects to fit one page.")
            projects = []
        if len(experience) > 2:
            reasons.append("Trimmed experience roles for page budget.")
            experience = experience[:2]
        for e in experience:
            e.bullets = e.bullets[:2]
    doc.experience = experience
    doc.projects = projects
    doc.omit_reasons = reasons
    return doc
