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
# Title patterns intentionally avoid bare "platform" / "infrastructure" /
# "systems engineer" / "build engineer" (too many non-software hits).
# Keywords split into strong (count 1) vs weak (count 0.5, never alone).
_FAMILY_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "id": "cloud_platform",
        "label": "Cloud / Platform / Infrastructure",
        "title_patterns": (
            r"\b(platform engineer|cloud engineer|infrastructure engineer|"
            r"cloud(?:\s|/|-)?(?:platform|infrastructure)|sre\b|"
            r"site reliability|devops engineer|systems? software engineer)\b",
        ),
        "strong_keywords": (
            "kubernetes",
            "k8s",
            "terraform",
            "aws",
            "gcp",
            "azure",
            "docker",
            "ci/cd",
            "helm",
            "eks",
            "gke",
        ),
        "weak_keywords": ("infrastructure", "platform", "cloud"),
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
            r"release engineer|ci/?cd engineer)\b",
        ),
        "strong_keywords": (
            "observability",
            "prometheus",
            "grafana",
            "jenkins",
            "ansible",
            "on-call",
            "incident response",
            "sre",
            "devops",
        ),
        "weak_keywords": ("monitoring", "incident", "pipeline"),
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
            r"\b(backend|back[- ]end|api engineer|server[- ]side|"
            r"services? engineer)\b",
        ),
        "strong_keywords": (
            "backend",
            "microservices",
            "grpc",
            "postgresql",
            "fastapi",
            "django",
            "flask",
            "spring boot",
            "rest api",
        ),
        "weak_keywords": ("api", "rest", "database", "sql"),
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
            r"\b(software engineer|software developer|swe\b|full[- ]?stack|"
            r"application engineer|software development engineer)\b",
        ),
        "strong_keywords": (
            "software engineer",
            "software development",
            "typescript",
            "javascript",
            "golang",
            "c++",
            "python",
            "java",
        ),
        "weak_keywords": ("linux", "coding", "programming"),
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
            r"\b(distributed systems?|systems? software|scalability engineer)\b",
        ),
        "strong_keywords": (
            "distributed systems",
            "kafka",
            "consensus",
            "raft",
            "event-driven",
            "message queue",
            "rabbitmq",
        ),
        "weak_keywords": ("distributed", "scalability", "throughput", "latency", "queue"),
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
            r"\b(machine learning engineer|ml engineer|mlops|ai engineer|"
            r"deep learning engineer|ml systems)\b",
        ),
        "strong_keywords": (
            "pytorch",
            "tensorflow",
            "mlops",
            "model training",
            "inference",
            "deep learning",
            "scikit",
        ),
        "weak_keywords": ("model", "training", "machine learning"),
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
            r"\b(account executive|account manager|sales development|"
            r"sales (?:rep|representative|manager|engineer|associate|director|specialist)|"
            r"business development|bdr\b|sdr\b|quota|pipeline generation|"
            r"enterprise sales|inside sales|customer success manager)\b",
            re.I,
        ),
    ),
    (
        "recruiting",
        re.compile(
            r"\b(recruiter|recruiting|talent acquisition|talent (?:partner|"
            r"strategist|sourcer|manager)|sourcer|people partner|"
            r"hr business partner|people operations|human resources|"
            r"university recruiter|technical recruiter)\b",
            re.I,
        ),
    ),
    (
        "finance",
        re.compile(
            r"\b(accountant|bookkeeper|controller|cpa\b|financial analyst|"
            r"finance analyst|program finance|strategic finance|product finance|"
            r"finance (?:manager|director|specialist)|"
            r"investment banker|audit associate|accounts payable|"
            r"accounts receivable|financial controller|fp&a\b|"
            r"treasury analyst)\b",
            re.I,
        ),
    ),
    (
        "marketing",
        re.compile(
            r"\b(marketing manager|growth marketer|brand manager|"
            r"content marketer|seo specialist|demand gen|"
            r"product marketing|growth marketing|field marketing)\b",
            re.I,
        ),
    ),
    (
        "legal",
        re.compile(
            r"\b(attorney|lawyer|counsel\b|paralegal|legal operations|"
            r"legal counsel|general counsel|privacy counsel|"
            r"product counsel|employment attorney|contract manager)\b",
            re.I,
        ),
    ),
    (
        "procurement",
        re.compile(
            r"\b(buyer\b|purchasing|procurement|category manager|"
            r"vendor manager|strategic sourcing)\b",
            re.I,
        ),
    ),
    (
        "hr_facilities",
        re.compile(
            r"\b(office manager|facilities manager|workplace experience|"
            r"people experience|hr coordinator|benefits specialist|"
            r"compensation analyst|office (?:&|and) events)\b",
            re.I,
        ),
    ),
    (
        "ops_logistics",
        re.compile(
            r"\b(logistics specialist|supply manager|supply chain|"
            r"warehouse|events manager|field operations|"
            r"kitchen operations|operations specialist|"
            r"global trade manager|production planning)\b",
            re.I,
        ),
    ),
    (
        "manufacturing_mech",
        re.compile(
            r"\b(manufacturing engineer|mechanical engineer|avionics|"
            r"composites|energetics|machine maintenance|ehs\b|"
            r"environmental health|technician|dfma|mass properties|"
            r"static structures|vehicle performance|gnc intern|"
            r"harness engineer|quality engineer|supplier quality|"
            r"sourcing specialist|electrical engineer|"
            r"pcb design|aircraft configuration)\b",
            re.I,
        ),
    ),
    (
        "program_non_swe",
        re.compile(
            r"\b(program manager|project manager|scrum master|"
            r"technical program manager|capture manager|"
            r"engineering program manager)\b",
            re.I,
        ),
    ),
    (
        "design_non_swe",
        re.compile(
            r"\b(product designer|ux designer|ui designer|graphic designer|"
            r"visual designer|design manager)\b",
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

# Titles that are clearly non-software unless the title itself carries SWE tokens.
_CLEAR_NON_SOFTWARE_TITLE = re.compile(
    r"\b(attorney|lawyer|paralegal|counsel\b|accountant|bookkeeper|"
    r"recruiter|talent (?:partner|strategist|acquisition)|"
    r"account executive|sales development|buyer\b|purchasing|"
    r"procurement|marketing manager|events manager|logistics|"
    r"warehouse|accounts payable|financial analyst|finance analyst|"
    r"ehs\b|avionics technician|composites|kitchen operations|"
    r"driver\b|cashier|nurse|teacher|mechanical engineer|"
    r"manufacturing engineer|office manager|facilities)\b",
    re.I,
)
_SOFTWARE_TITLE_TOKEN = re.compile(
    r"\b(software|swe\b|backend|front[- ]?end|full[- ]?stack|devops|sre\b|"
    r"platform engineer|infrastructure engineer|ml eng|machine learning|"
    r"data engineer|site reliability|cloud engineer|distributed|"
    r"firmware|embedded software)\b",
    re.I,
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
    """Legacy integer hit count (weak+strong keywords equally)."""
    blob = description.lower()
    hits = 0
    for kw in family.responsibility_keywords:
        if kw.lower() in blob:
            hits += 1
    return hits


def _seed_for_family(family_id: str) -> dict[str, Any] | None:
    for seed in _FAMILY_SEEDS:
        if seed["id"] == family_id:
            return seed
    return None


def _family_desc_score(family: RoleFamily, description: str) -> float:
    """Weighted description overlap: strong keywords count 1, weak 0.5."""
    blob = description.lower()
    seed = _seed_for_family(family.id)
    if seed is None:
        return float(_family_desc_match(family, description))
    score = 0.0
    for kw in seed.get("strong_keywords") or ():
        if str(kw).lower() in blob:
            score += 1.0
    for kw in seed.get("weak_keywords") or ():
        if str(kw).lower() in blob:
            score += 0.5
    return score


def classify_job_against_profile(
    job: JobPosting,
    profile: RoleProfile,
    *,
    candidate_blob: str = "",
    skip_overrides: bool = False,
    relevance_overrides: Any | None = None,
) -> dict[str, Any]:
    """Layer-1: cheap role-family gate. Does not change fit scoring."""
    title = job.title or ""
    description = job.description_text or ""
    enabled = profile.enabled_families()

    if not skip_overrides:
        from packages.core.services.role_relevance_overrides import (
            REASON_USER_NOT_RELEVANT,
            override_matches_job,
        )

        hit = override_matches_job(job, relevance_overrides)
        if hit is not None:
            return {
                "matched_family": None,
                "status": STATUS_MISMATCH,
                "reason": REASON_USER_NOT_RELEVANT,
                "user_override": True,
            }

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

    soft_title = bool(_SOFTWARE_TITLE_TOKEN.search(title))
    soft_desc = bool(
        re.search(
            r"\b(software engineer|backend|kubernetes|terraform|microservices|"
            r"distributed systems|devops|site reliability|pytorch|fastapi)\b",
            description,
            flags=re.I,
        )
    )

    # Clear non-software titles never pass on boilerplate alone.
    if _CLEAR_NON_SOFTWARE_TITLE.search(title) and not soft_title:
        return {
            "matched_family": None,
            "status": STATUS_MISMATCH,
            "reason": "Non-software title without software title signal",
        }

    for label, pattern in _OUTSIDE_PATTERNS:
        title_hit = bool(pattern.search(title))
        desc_hit = bool(description and pattern.search(description[:2500]))
        if not title_hit and not desc_hit:
            continue
        if label in {
            "hardware_design",
            "manufacturing_mech",
            "program_non_swe",
            "design_non_swe",
        }:
            # Title hit without software title token → mismatch.
            # Description-only outside: ignore when JD is clearly software.
            if title_hit and not soft_title:
                return {
                    "matched_family": None,
                    "status": STATUS_MISMATCH,
                    "reason": f"Outside role family: {label}",
                }
            if desc_hit and not soft_title and not soft_desc:
                return {
                    "matched_family": None,
                    "status": STATUS_MISMATCH,
                    "reason": f"Outside role family: {label}",
                }
            continue
        # Sales/recruiting/finance/marketing/legal/ops: title match always excludes.
        if title_hit or (desc_hit and not soft_title and not soft_desc):
            return {
                "matched_family": None,
                "status": STATUS_MISMATCH,
                "reason": f"Outside role family: {label}",
            }

    best_id: str | None = None
    best_score = 0.0
    best_title_hit = False
    for fam in enabled:
        score = 0.0
        title_hit = _family_title_match(fam, title)
        if title_hit:
            score += 3.0
        desc_score = _family_desc_score(fam, description)
        # Cap description contribution; weak keywords alone cannot reach threshold.
        score += min(3.0, desc_score)
        if score > best_score:
            best_score = score
            best_id = fam.id
            best_title_hit = title_hit

    # Require a real title family hit, or strong description evidence (≥2.5).
    if best_title_hit and best_score >= 3.0:
        return {
            "matched_family": best_id,
            "status": STATUS_MATCHED,
            "reason": f"Matched profile family '{best_id}'",
        }
    if not best_title_hit and best_score >= 3.5 and soft_desc:
        return {
            "matched_family": best_id,
            "status": STATUS_MATCHED,
            "reason": f"Matched profile family '{best_id}'",
        }

    # Unfamiliar title: keep pass when description has strong family evidence.
    # Do not pass on weak boilerplate keywords alone (~20% prior mismatch).
    if description.strip() and enabled and not _CLEAR_NON_SOFTWARE_TITLE.search(title):
        for fam in enabled:
            strong = _family_desc_score(fam, description)
            if strong >= 2.5 and (soft_desc or soft_title):
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

    # Sparse / unknown titles: allow Layer-2 only when title itself is software-ish.
    if len(description.strip()) < 80:
        if soft_title:
            return {
                "matched_family": None,
                "status": STATUS_MATCHED,
                "reason": "Sparse description with software title; deferring to Layer-2",
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
    block: dict[str, Any] = {
        "matched_family": result.get("matched_family"),
        "status": result.get("status"),
        "reason": result.get("reason"),
    }
    if result.get("user_override"):
        block["user_override"] = True
    meta["role_profile"] = block
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
) -> dict[str, Any]:
    """Layer-2: score newly unscored jobs that pass Layer-1 (deterministic)."""
    from packages.core.services.evidence_fit_service import (
        assess_and_store,
        get_stored_fit,
    )

    profile = load_role_profile(session)
    from apps.resume_generation.services.resume_fact_service import usable_facts

    facts = usable_facts(session)
    blob = _evidence_blob(facts)

    jobs = (
        session.query(JobPosting)
        .order_by(JobPosting.created_at.desc())
        .limit(max(limit * 8, 200))
        .all()
    )
    assessed = 0
    skipped_mismatch = 0
    skipped_scored = 0
    bands: dict[str, int] = {}

    for job in jobs:
        if assessed >= limit:
            break
        if get_stored_fit(job):
            skipped_scored += 1
            continue
        block = get_stored_role_block(job)
        if not block or block.get("status") not in {STATUS_MATCHED, STATUS_MISMATCH}:
            result = classify_job_against_profile(job, profile, candidate_blob=blob)
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
        print(
            f"  [{assessed}/{limit}] {job.title[:60]} → "
            f"{assessment.band} ({assessment.score})",
            flush=True,
        )

    session.flush()
    return {
        "assessed": assessed,
        "skipped_scored": skipped_scored,
        "skipped_mismatch": skipped_mismatch,
        "role_filtered_only": role_filtered_only,
        "bands": bands,
    }


def _job_company_label(job: JobPosting) -> str:
    company = getattr(job, "company", None)
    if company is not None:
        name = getattr(company, "canonical_name", None)
        if name:
            return str(name)
    meta = job.intake_metadata or {}
    for key in ("company_name", "company"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


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


def audit_role_feed_samples(
    session: Session,
    *,
    per_bucket: int = 20,
    reclassify: bool = False,
) -> dict[str, Any]:
    """Sample jobs from role-matched / Recommended / Stretch for QA audit.

    Prints-friendly structure: title, company, family (and fit band when present).
    When reclassify=True, Layer-1 is recomputed in-memory (not persisted).
    """
    from sqlalchemy.orm import joinedload

    from packages.core.services.evidence_fit_service import (
        BAND_RECOMMENDED,
        BAND_STRETCH,
        get_stored_fit,
    )
    from packages.core.services.job_eligibility import (
        ELIGIBILITY_ACTIONABLE,
        resolve_job_eligibility,
    )

    profile = load_role_profile(session, refresh=False)
    blob = ""
    if reclassify:
        from apps.resume_generation.services.resume_fact_service import usable_facts

        blob = _evidence_blob(usable_facts(session))

    jobs = (
        session.query(JobPosting)
        .options(joinedload(JobPosting.company))
        .order_by(JobPosting.created_at.desc())
        .limit(5000)
        .all()
    )

    role_matched: list[dict[str, Any]] = []
    recommended: list[dict[str, Any]] = []
    stretch: list[dict[str, Any]] = []

    for job in jobs:
        block = get_stored_role_block(job) or {}
        if reclassify:
            result = classify_job_against_profile(job, profile, candidate_blob=blob)
            status = result.get("status")
            family = result.get("matched_family")
            reason = result.get("reason")
        else:
            status = block.get("status")
            family = block.get("matched_family")
            reason = block.get("reason")

        company = _job_company_label(job)
        base = {
            "title": job.title or "",
            "company": company,
            "family": family,
            "reason": reason,
            "job_id": job.id,
        }

        if (
            status == STATUS_MATCHED
            and len(role_matched) < per_bucket
        ):
            role_matched.append(base)

        if status == STATUS_MISMATCH:
            continue
        elig = resolve_job_eligibility(job)
        if elig.result != ELIGIBILITY_ACTIONABLE:
            continue
        fit = get_stored_fit(job) or {}
        band = fit.get("band")
        scored = {
            **base,
            "family": family or block.get("matched_family"),
            "fit_band": band,
            "fit_score": fit.get("score"),
        }
        if band == BAND_RECOMMENDED and len(recommended) < per_bucket:
            recommended.append(scored)
        elif band == BAND_STRETCH and len(stretch) < per_bucket:
            stretch.append(scored)

        if (
            len(role_matched) >= per_bucket
            and len(recommended) >= per_bucket
            and len(stretch) >= per_bucket
        ):
            break

    return {
        "per_bucket": per_bucket,
        "reclassify": reclassify,
        "family_ids": [f.id for f in profile.enabled_families()],
        "buckets": {
            "role_matched": role_matched,
            "recommended": recommended,
            "stretch": stretch,
        },
    }
