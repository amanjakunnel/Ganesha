"""Derive and persist a candidate role-family profile from resume evidence.

Stored outside the repo at ~/.ganesha/role_profile.json. User overrides
(add / remove / disable) win over derivation on merge. Eligibility and
evidence-fit scoring are untouched — this is a cheap Layer-1 pre-filter.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from packages.core.domain.models import JobPosting, ResumeFact, ResumeSourceDocument

ROLE_PROFILE_DIR = Path.home() / ".ganesha"
ROLE_PROFILE_PATH = ROLE_PROFILE_DIR / "role_profile.json"
ROLE_PROFILE_VERSION = 1

STATUS_MATCHED = "matched"
STATUS_MISMATCH = "role_family_mismatch"
STATUS_UNSCORED = "unscored"

# Seed matchers used only when evidence supports them — never emitted wholesale.
_FAMILY_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "id": "cloud_platform",
        "label": "Cloud / Platform / Infrastructure",
        "title_patterns": (
            r"\b(platform|infrastructure|cloud|sre|site reliability|"
            r"devops|systems? engineer)\b",
        ),
        "responsibility_keywords": (
            "kubernetes",
            "aws",
            "gcp",
            "azure",
            "terraform",
            "infrastructure",
            "ci/cd",
            "docker",
            "platform",
        ),
        "evidence_signals": (
            r"\b(kubernetes|k8s|aws|gcp|azure|terraform|docker|ci/?cd|"
            r"infrastructure|platform engineer|devops)\b",
        ),
        "track_boosts": ("cloud",),
    },
    {
        "id": "devops_sre",
        "label": "DevOps / SRE",
        "title_patterns": (
            r"\b(devops|sre|site reliability|reliability engineer|"
            r"release engineer|build engineer)\b",
        ),
        "responsibility_keywords": (
            "observability",
            "monitoring",
            "on-call",
            "incident",
            "jenkins",
            "ansible",
            "prometheus",
            "grafana",
        ),
        "evidence_signals": (
            r"\b(devops|sre|jenkins|ansible|prometheus|grafana|"
            r"observability|on[- ]call|ci/?cd|pipeline)\b",
        ),
        "track_boosts": ("cloud",),
    },
    {
        "id": "backend",
        "label": "Backend Engineering",
        "title_patterns": (
            r"\b(backend|back[- ]end|api engineer|server[- ]side)\b",
        ),
        "responsibility_keywords": (
            "api",
            "backend",
            "microservices",
            "rest",
            "grpc",
            "database",
            "postgresql",
            "sql",
        ),
        "evidence_signals": (
            r"\b(backend|rest\s*api|api|microservices?|grpc|postgresql|"
            r"sql|django|flask|fastapi|spring)\b",
        ),
        "track_boosts": ("dev", "csharp"),
    },
    {
        "id": "software_engineering",
        "label": "General Software Engineering",
        "title_patterns": (
            r"\b(software engineer|swe|full[- ]?stack|application engineer)\b",
        ),
        "responsibility_keywords": (
            "software engineer",
            "python",
            "java",
            "c++",
            "golang",
            "typescript",
            "javascript",
            "linux",
        ),
        "evidence_signals": (
            r"\b(software engineer|python|java|c\+\+|golang|\bgo\b|"
            r"typescript|javascript|linux)\b",
        ),
        "track_boosts": ("dev", "general", "csharp"),
    },
    {
        "id": "distributed_systems",
        "label": "Distributed Systems",
        "title_patterns": (
            r"\b(distributed systems?|systems? software|scalability)\b",
        ),
        "responsibility_keywords": (
            "distributed",
            "consensus",
            "kafka",
            "queue",
            "scalability",
            "throughput",
            "latency",
        ),
        "evidence_signals": (
            r"\b(distributed|kafka|rabbitmq|scalability|throughput|"
            r"latency|message queue|event[- ]driven)\b",
        ),
        "track_boosts": ("dev", "cloud"),
    },
    {
        "id": "ml_systems",
        "label": "ML / Systems",
        "title_patterns": (
            r"\b(machine learning|ml engineer|mlops|ai engineer|"
            r"deep learning)\b",
        ),
        "responsibility_keywords": (
            "pytorch",
            "tensorflow",
            "model",
            "mlops",
            "inference",
            "training",
        ),
        "evidence_signals": (
            r"\b(pytorch|tensorflow|machine learning|mlops|model training|"
            r"inference|deep learning|scikit)\b",
        ),
        "track_boosts": ("ml",),
    },
)

# Clear outsides — title/desc signals that do not belong in the profile.
_OUTSIDE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "sales",
        re.compile(
            r"\b(account executive|sales (?:rep|representative|manager|engineer)|"
            r"business development|bdr\b|sdr\b|quota|pipeline generation)\b",
            re.I,
        ),
    ),
    (
        "recruiting",
        re.compile(
            r"\b(recruiter|recruiting|talent acquisition|sourcer|"
            r"people partner|hr business partner)\b",
            re.I,
        ),
    ),
    (
        "finance",
        re.compile(
            r"\b(accountant|bookkeeper|controller|cpa\b|financial analyst|"
            r"investment banker|audit associate)\b",
            re.I,
        ),
    ),
    (
        "marketing",
        re.compile(
            r"\b(marketing manager|growth marketer|brand manager|"
            r"content marketer|seo specialist|demand gen)\b",
            re.I,
        ),
    ),
    (
        "hardware_design",
        re.compile(
            r"\b(asic|fpga|rtl|verilog|vhdl|pcb layout|analog design|"
            r"hardware design engineer|digital design engineer)\b",
            re.I,
        ),
    ),
)


@dataclass
class RoleFamily:
    id: str
    label: str
    title_patterns: list[str] = field(default_factory=list)
    responsibility_keywords: list[str] = field(default_factory=list)
    evidence_fact_ids: list[str] = field(default_factory=list)
    evidence_excerpts: list[str] = field(default_factory=list)
    enabled: bool = True
    source: str = "derived"  # derived | custom

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RoleFamily:
        return cls(
            id=str(raw.get("id") or ""),
            label=str(raw.get("label") or raw.get("id") or ""),
            title_patterns=[str(x) for x in (raw.get("title_patterns") or [])],
            responsibility_keywords=[
                str(x) for x in (raw.get("responsibility_keywords") or [])
            ],
            evidence_fact_ids=[str(x) for x in (raw.get("evidence_fact_ids") or [])],
            evidence_excerpts=[str(x) for x in (raw.get("evidence_excerpts") or [])],
            enabled=bool(raw.get("enabled", True)),
            source=str(raw.get("source") or "derived"),
        )


@dataclass
class UserOverrides:
    disabled_ids: list[str] = field(default_factory=list)
    removed_ids: list[str] = field(default_factory=list)
    added_families: list[RoleFamily] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "disabled_ids": list(self.disabled_ids),
            "removed_ids": list(self.removed_ids),
            "added_families": [f.to_dict() for f in self.added_families],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> UserOverrides:
        if not isinstance(raw, dict):
            return cls()
        added: list[RoleFamily] = []
        for item in raw.get("added_families") or []:
            if isinstance(item, dict) and item.get("id"):
                fam = RoleFamily.from_dict(item)
                fam.source = "custom"
                added.append(fam)
        return cls(
            disabled_ids=[str(x) for x in (raw.get("disabled_ids") or [])],
            removed_ids=[str(x) for x in (raw.get("removed_ids") or [])],
            added_families=added,
        )


@dataclass
class RoleProfile:
    version: int = ROLE_PROFILE_VERSION
    evidence_hash: str = ""
    families: list[RoleFamily] = field(default_factory=list)
    user_overrides: UserOverrides = field(default_factory=UserOverrides)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "evidence_hash": self.evidence_hash,
            "families": [f.to_dict() for f in self.families],
            "user_overrides": self.user_overrides.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RoleProfile:
        families = [
            RoleFamily.from_dict(item)
            for item in (raw.get("families") or [])
            if isinstance(item, dict) and item.get("id")
        ]
        return cls(
            version=int(raw.get("version") or ROLE_PROFILE_VERSION),
            evidence_hash=str(raw.get("evidence_hash") or ""),
            families=families,
            user_overrides=UserOverrides.from_dict(raw.get("user_overrides")),
        )

    def enabled_families(self) -> list[RoleFamily]:
        return [f for f in self.families if f.enabled]


def evidence_hash_for_facts(facts: list[ResumeFact]) -> str:
    parts: list[str] = []
    for fact in sorted(facts, key=lambda f: f.id):
        skills = ",".join(sorted(fact.skills_json or []))
        parts.append(f"{fact.id}|{fact.normalized_text}|{skills}")
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:24]


def _evidence_blob(facts: list[ResumeFact]) -> str:
    chunks: list[str] = []
    for fact in facts:
        chunks.append(fact.normalized_text or fact.source_text or "")
        if fact.role_title:
            chunks.append(fact.role_title)
        if fact.skills_json:
            chunks.append(" ".join(fact.skills_json))
    return "\n".join(chunks).lower()


def _tracks_for_facts(session: Session, facts: list[ResumeFact]) -> set[str]:
    doc_ids = {f.source_document_id for f in facts if f.source_document_id}
    if not doc_ids:
        return set()
    rows = (
        session.query(ResumeSourceDocument.track)
        .filter(ResumeSourceDocument.id.in_(doc_ids))
        .all()
    )
    return {(row[0] or "").lower() for row in rows if row and row[0]}


def derive_role_profile(
    session: Session,
    *,
    facts: list[ResumeFact] | None = None,
) -> RoleProfile:
    """Build families the candidate evidence actually supports."""
    from apps.resume_generation.services.resume_fact_service import usable_facts

    resolved = facts if facts is not None else usable_facts(session)
    blob = _evidence_blob(resolved)
    tracks = _tracks_for_facts(session, resolved)
    ehash = evidence_hash_for_facts(resolved)
    families: list[RoleFamily] = []

    for seed in _FAMILY_SEEDS:
        signals: tuple[str, ...] = tuple(seed["evidence_signals"])
        hits: list[tuple[str, str]] = []
        for fact in resolved:
            text = f"{fact.normalized_text or ''} {' '.join(fact.skills_json or [])}"
            if any(re.search(pat, text, flags=re.I) for pat in signals):
                excerpt = (fact.source_text or fact.normalized_text or "")[:160]
                hits.append((fact.id, excerpt))
        track_hit = bool(set(seed.get("track_boosts") or ()) & tracks)
        # Require real evidence hits; tracks alone only boost borderline cases.
        if len(hits) < 1 and not (track_hit and re.search(signals[0], blob, flags=re.I)):
            continue
        if len(hits) < 1:
            continue
        families.append(
            RoleFamily(
                id=str(seed["id"]),
                label=str(seed["label"]),
                title_patterns=list(seed["title_patterns"]),
                responsibility_keywords=list(seed["responsibility_keywords"]),
                evidence_fact_ids=[h[0] for h in hits[:8]],
                evidence_excerpts=[h[1] for h in hits[:5]],
                enabled=True,
                source="derived",
            )
        )

    return RoleProfile(
        version=ROLE_PROFILE_VERSION,
        evidence_hash=ehash,
        families=families,
        user_overrides=UserOverrides(),
    )


def merge_profile(derived: RoleProfile, overrides: UserOverrides) -> RoleProfile:
    """Apply user add/remove/disable on top of a derived profile."""
    removed = set(overrides.removed_ids)
    disabled = set(overrides.disabled_ids)
    merged: list[RoleFamily] = []
    seen: set[str] = set()

    for fam in derived.families:
        if fam.id in removed:
            continue
        copy = RoleFamily.from_dict(fam.to_dict())
        if fam.id in disabled:
            copy.enabled = False
        merged.append(copy)
        seen.add(fam.id)

    for custom in overrides.added_families:
        if not custom.id or custom.id in removed:
            continue
        copy = RoleFamily.from_dict(custom.to_dict())
        copy.source = "custom"
        if custom.id in disabled:
            copy.enabled = False
        if custom.id in seen:
            # Custom replacement wins label/patterns.
            merged = [copy if m.id == custom.id else m for m in merged]
        else:
            merged.append(copy)
            seen.add(custom.id)

    return RoleProfile(
        version=ROLE_PROFILE_VERSION,
        evidence_hash=derived.evidence_hash,
        families=merged,
        user_overrides=overrides,
    )


def _load_raw(path: Path = ROLE_PROFILE_PATH) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_raw(payload: dict[str, Any], path: Path = ROLE_PROFILE_PATH) -> None:
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


def load_role_profile(
    session: Session | None = None,
    *,
    path: Path = ROLE_PROFILE_PATH,
    refresh: bool = False,
) -> RoleProfile:
    """Load merged profile; derive (+ persist) when missing or refresh=True."""
    raw = _load_raw(path)
    overrides = UserOverrides.from_dict(raw.get("user_overrides") if raw else None)

    if session is None:
        if raw:
            return RoleProfile.from_dict(raw)
        return RoleProfile(user_overrides=overrides)

    derived = derive_role_profile(session)
    if (
        raw
        and not refresh
        and raw.get("evidence_hash") == derived.evidence_hash
        and raw.get("families")
    ):
        # Keep stored derived families; still re-apply overrides.
        base_families = [
            RoleFamily.from_dict(item)
            for item in (raw.get("families") or [])
            if isinstance(item, dict)
            and item.get("id")
            and item.get("source") != "custom"
        ]
        base = RoleProfile(
            version=ROLE_PROFILE_VERSION,
            evidence_hash=derived.evidence_hash,
            families=base_families or derived.families,
        )
        return merge_profile(base, overrides)

    merged = merge_profile(derived, overrides)
    _save_raw(merged.to_dict(), path)
    return merged


def save_role_profile_overrides(
    overrides: UserOverrides | dict[str, Any],
    *,
    path: Path = ROLE_PROFILE_PATH,
    session: Session | None = None,
) -> RoleProfile:
    """Persist user overrides and rewrite the merged profile file."""
    ov = (
        overrides
        if isinstance(overrides, UserOverrides)
        else UserOverrides.from_dict(overrides)
    )
    raw = _load_raw(path) or {}
    if session is not None:
        derived = derive_role_profile(session)
    else:
        # Keep previously derived families when no session is available.
        families = [
            RoleFamily.from_dict(item)
            for item in (raw.get("families") or [])
            if isinstance(item, dict) and item.get("id") and item.get("source") != "custom"
        ]
        derived = RoleProfile(
            version=ROLE_PROFILE_VERSION,
            evidence_hash=str(raw.get("evidence_hash") or ""),
            families=families,
        )
    merged = merge_profile(derived, ov)
    _save_raw(merged.to_dict(), path)
    return merged


def get_stored_role_block(job: JobPosting) -> dict[str, Any] | None:
    meta = job.intake_metadata or {}
    block = meta.get("role_profile")
    return block if isinstance(block, dict) else None


def is_role_family_mismatch(job: JobPosting) -> bool:
    block = get_stored_role_block(job) or {}
    return block.get("status") == STATUS_MISMATCH


def _family_title_match(family: RoleFamily, title: str) -> bool:
    for pat in family.title_patterns:
        try:
            if re.search(pat, title, flags=re.I):
                return True
        except re.error:
            if pat.lower() in title.lower():
                return True
    return False


def _family_desc_match(family: RoleFamily, description: str) -> int:
    blob = description.lower()
    hits = 0
    for kw in family.responsibility_keywords:
        if kw.lower() in blob:
            hits += 1
    return hits


def classify_job_against_profile(
    job: JobPosting,
    profile: RoleProfile,
    *,
    candidate_blob: str = "",
) -> dict[str, Any]:
    """Layer-1: cheap role-family gate. Does not change fit scoring."""
    title = job.title or ""
    description = job.description_text or ""
    enabled = profile.enabled_families()

    # Hardware / specialist outsides — reuse evidence_fit detector when possible.
    if candidate_blob:
        from packages.core.services.evidence_fit_service import detect_domain_mismatch

        mismatch, reason = detect_domain_mismatch(title, description, candidate_blob)
        if mismatch:
            return {
                "matched_family": None,
                "status": STATUS_MISMATCH,
                "reason": reason or "Specialist domain outside candidate evidence",
            }

    soft_desc = bool(
        re.search(
            r"\b(software|backend|platform|api|kubernetes|python|java|"
            r"distributed|devops|infrastructure)\b",
            description,
            flags=re.I,
        )
    )
    for label, pattern in _OUTSIDE_PATTERNS:
        title_hit = bool(pattern.search(title))
        desc_hit = bool(description and pattern.search(description[:2500]))
        if not title_hit and not desc_hit:
            continue
        if label == "hardware_design":
            # Title-only hardware without software context → mismatch.
            if title_hit and not soft_desc:
                return {
                    "matched_family": None,
                    "status": STATUS_MISMATCH,
                    "reason": f"Outside role family: {label}",
                }
            continue
        # Sales/recruiting/finance/marketing: title match always excludes.
        # Description-only match excludes unless the JD is clearly software.
        if title_hit or (desc_hit and not soft_desc):
            return {
                "matched_family": None,
                "status": STATUS_MISMATCH,
                "reason": f"Outside role family: {label}",
            }

    best_id: str | None = None
    best_score = 0
    for fam in enabled:
        score = 0
        if _family_title_match(fam, title):
            score += 3
        desc_hits = _family_desc_match(fam, description)
        score += min(3, desc_hits)
        if score > best_score:
            best_score = score
            best_id = fam.id

    if best_score >= 3:
        return {
            "matched_family": best_id,
            "status": STATUS_MATCHED,
            "reason": f"Matched profile family '{best_id}'",
        }

    # Unfamiliar title: description keyword overlap with enabled families → pass.
    if description.strip() and enabled:
        for fam in enabled:
            if _family_desc_match(fam, description) >= 2:
                return {
                    "matched_family": fam.id,
                    "status": STATUS_MATCHED,
                    "reason": (
                        f"Unfamiliar title; description matches family '{fam.id}'"
                    ),
                }

    # No enabled families yet — do not block Layer-2.
    if not enabled:
        return {
            "matched_family": None,
            "status": STATUS_MATCHED,
            "reason": "No role families enabled; allowing Layer-2",
        }

    # Sparse / unknown titles: allow Layer-2 only when description has
    # at least one software-ish token from the enabled profile; otherwise
    # park in Other roles (never delete).
    if len(description.strip()) < 80:
        soft = re.search(
            r"\b(software|backend|platform|api|kubernetes|python|java|"
            r"distributed|devops|infrastructure|sre|cloud)\b",
            f"{title}\n{description}",
            flags=re.I,
        )
        if soft:
            return {
                "matched_family": None,
                "status": STATUS_MATCHED,
                "reason": "Sparse description with software signal; deferring to Layer-2",
            }
        return {
            "matched_family": None,
            "status": STATUS_MISMATCH,
            "reason": "Sparse non-software listing; no profile overlap",
        }

    return {
        "matched_family": None,
        "status": STATUS_MISMATCH,
        "reason": "No overlap with enabled role families",
    }


def store_role_classification(job: JobPosting, result: dict[str, Any]) -> None:
    meta = dict(job.intake_metadata or {})
    meta["role_profile"] = {
        "matched_family": result.get("matched_family"),
        "status": result.get("status"),
        "reason": result.get("reason"),
    }
    job.intake_metadata = meta


def classify_and_store(
    session: Session,
    job: JobPosting,
    profile: RoleProfile | None = None,
    *,
    candidate_blob: str = "",
) -> dict[str, Any]:
    resolved = profile or load_role_profile(session)
    if not candidate_blob:
        from apps.resume_generation.services.resume_fact_service import usable_facts

        facts = usable_facts(session)
        candidate_blob = _evidence_blob(facts)
    result = classify_job_against_profile(
        job, resolved, candidate_blob=candidate_blob
    )
    store_role_classification(job, result)
    session.add(job)
    return result


def classify_jobs_batch(
    session: Session,
    *,
    limit: int = 100,
    apply: bool = False,
    only_missing: bool = True,
) -> dict[str, Any]:
    profile = load_role_profile(session, refresh=False)
    from apps.resume_generation.services.resume_fact_service import usable_facts

    facts = usable_facts(session)
    blob = _evidence_blob(facts)
    q = session.query(JobPosting).order_by(JobPosting.created_at.desc())
    jobs = q.limit(max(limit * 4, limit)).all() if only_missing else q.limit(limit).all()
    scanned = 0
    applied = 0
    counts = {STATUS_MATCHED: 0, STATUS_MISMATCH: 0}
    examples: dict[str, list[str]] = {STATUS_MATCHED: [], STATUS_MISMATCH: []}

    for job in jobs:
        if scanned >= limit:
            break
        existing = get_stored_role_block(job)
        if only_missing and existing and existing.get("status") in {
            STATUS_MATCHED,
            STATUS_MISMATCH,
        }:
            continue
        scanned += 1
        result = classify_job_against_profile(job, profile, candidate_blob=blob)
        status = str(result.get("status") or STATUS_UNSCORED)
        counts[status] = counts.get(status, 0) + 1
        if len(examples.get(status, [])) < 5:
            examples.setdefault(status, []).append(job.title or "(untitled)")
        if apply:
            store_role_classification(job, result)
            session.add(job)
            applied += 1

    if apply:
        session.flush()

    return {
        "scanned": scanned,
        "applied": applied,
        "dry_run": not apply,
        "evidence_hash": profile.evidence_hash,
        "family_ids": [f.id for f in profile.enabled_families()],
        "counts": counts,
        "examples": examples,
    }


def assess_fit_batch(
    session: Session,
    *,
    limit: int = 50,
    role_filtered_only: bool = True,
    eligible_only: bool = True,
    progress_cb: Any | None = None,
) -> dict[str, Any]:
    """Layer-2: score newly unscored jobs that pass Layer-1 (deterministic).

    Scans newest-first in windows until ``limit`` assessments or the feed is
    exhausted. Skips already-scored rows; optionally skips role mismatches and
    eligibility-excluded postings.
    """
    from packages.core.services.evidence_fit_service import (
        assess_and_store,
        get_stored_fit,
    )
    from packages.core.services.job_eligibility import (
        ELIGIBILITY_EXCLUDED,
        resolve_job_eligibility,
    )

    profile = load_role_profile(session)
    from apps.resume_generation.services.resume_fact_service import usable_facts

    facts = usable_facts(session)
    blob = _evidence_blob(facts)

    assessed = 0
    skipped_mismatch = 0
    skipped_scored = 0
    skipped_excluded = 0
    bands: dict[str, int] = {}
    window = max(limit * 8, 200)
    offset = 0

    while assessed < limit:
        jobs = (
            session.query(JobPosting)
            .order_by(JobPosting.created_at.desc())
            .offset(offset)
            .limit(window)
            .all()
        )
        if not jobs:
            break
        offset += len(jobs)

        for job in jobs:
            if assessed >= limit:
                break
            if get_stored_fit(job):
                skipped_scored += 1
                continue
            if eligible_only:
                elig = resolve_job_eligibility(job)
                if elig.result == ELIGIBILITY_EXCLUDED:
                    skipped_excluded += 1
                    continue
            block = get_stored_role_block(job)
            if not block or block.get("status") not in {
                STATUS_MATCHED,
                STATUS_MISMATCH,
            }:
                result = classify_job_against_profile(
                    job, profile, candidate_blob=blob
                )
                store_role_classification(job, result)
                session.add(job)
                block = result
            if block.get("status") == STATUS_MISMATCH:
                skipped_mismatch += 1
                if role_filtered_only:
                    continue
            assessment = assess_and_store(session, job)
            bands[assessment.band] = bands.get(assessment.band, 0) + 1
            assessed += 1
            msg = (
                f"  [{assessed}/{limit}] {(job.title or '')[:60]} → "
                f"{assessment.band} ({assessment.score})"
            )
            print(msg, flush=True)
            if progress_cb is not None:
                progress_cb(assessed, limit, job, assessment)

        if len(jobs) < window:
            break

    session.flush()
    return {
        "assessed": assessed,
        "skipped_scored": skipped_scored,
        "skipped_mismatch": skipped_mismatch,
        "skipped_excluded": skipped_excluded,
        "role_filtered_only": role_filtered_only,
        "eligible_only": eligible_only,
        "bands": bands,
    }


def assess_fit_drain(
    session: Session,
    *,
    batch_size: int = 200,
    role_filtered_only: bool = True,
    eligible_only: bool = True,
    max_batches: int | None = None,
) -> dict[str, Any]:
    """Keep running assess_fit_batch until no eligible unscored jobs remain."""
    total_assessed = 0
    bands: dict[str, int] = {}
    batches = 0
    skipped_scored = 0
    skipped_mismatch = 0
    skipped_excluded = 0

    exhausted = False
    while True:
        if max_batches is not None and batches >= max_batches:
            break
        batches += 1
        print(
            f"Drain batch {batches} (up to {batch_size})…",
            flush=True,
        )
        result = assess_fit_batch(
            session,
            limit=batch_size,
            role_filtered_only=role_filtered_only,
            eligible_only=eligible_only,
        )
        n = int(result.get("assessed") or 0)
        total_assessed += n
        skipped_scored += int(result.get("skipped_scored") or 0)
        skipped_mismatch += int(result.get("skipped_mismatch") or 0)
        skipped_excluded += int(result.get("skipped_excluded") or 0)
        for band, count in (result.get("bands") or {}).items():
            bands[band] = bands.get(band, 0) + int(count)
        print(
            f"  batch assessed={n} cumulative={total_assessed}",
            flush=True,
        )
        if n == 0:
            exhausted = True
            break

    return {
        "assessed": total_assessed,
        "batches": batches,
        "skipped_scored": skipped_scored,
        "skipped_mismatch": skipped_mismatch,
        "skipped_excluded": skipped_excluded,
        "role_filtered_only": role_filtered_only,
        "eligible_only": eligible_only,
        "bands": bands,
        "exhausted": exhausted,
    }


def feed_counts_snapshot(session: Session) -> dict[str, Any]:
    """Recommended / Stretch / Other roles counts + sample titles."""
    from packages.core.services.evidence_fit_service import (
        BAND_RECOMMENDED,
        BAND_STRETCH,
        get_stored_fit,
    )
    from packages.core.services.job_eligibility import (
        ELIGIBILITY_ACTIONABLE,
        ELIGIBILITY_EXCLUDED,
        resolve_job_eligibility,
    )

    recommended: list[str] = []
    stretch: list[str] = []
    other: list[str] = []
    counts = {
        "recommended_actionable": 0,
        "stretch_actionable": 0,
        "other_roles": 0,
        "unscored_fit": 0,
        "total": 0,
    }
    jobs = session.query(JobPosting).all()
    counts["total"] = len(jobs)
    for job in jobs:
        elig = resolve_job_eligibility(job)
        if is_role_family_mismatch(job):
            counts["other_roles"] += 1
            if len(other) < 5:
                other.append(job.title or "")
            continue
        if elig.result == ELIGIBILITY_EXCLUDED:
            continue
        fit = get_stored_fit(job)
        band = (fit or {}).get("band")
        if fit is None:
            counts["unscored_fit"] += 1
            continue
        if elig.result != ELIGIBILITY_ACTIONABLE:
            continue
        if band == BAND_RECOMMENDED:
            counts["recommended_actionable"] += 1
            if len(recommended) < 5:
                recommended.append(job.title or "")
        elif band == BAND_STRETCH:
            counts["stretch_actionable"] += 1
            if len(stretch) < 5:
                stretch.append(job.title or "")
    return {
        "counts": counts,
        "examples": {
            "recommended": recommended,
            "stretch": stretch,
            "other_roles": other,
        },
    }
