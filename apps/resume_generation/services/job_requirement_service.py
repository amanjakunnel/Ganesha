from __future__ import annotations

import re

from packages.core.domain.models import JobPosting, hash_text
from packages.core.services.job_scoring import TRACK_KEYWORDS
from apps.resume_generation.schemas import RequirementSet

MIN_DESCRIPTION_CHARS = 120


def extract_job_requirements(job: JobPosting) -> RequirementSet:
    blob = f"{job.title}\n{job.description_text or ''}"
    text = blob.lower()
    insufficient = len((job.description_text or "").strip()) < MIN_DESCRIPTION_CHARS

    scores = {
        track: sum(1 for kw in kws if kw in text)
        for track, kws in TRACK_KEYWORDS.items()
    }
    # csharp lightweight signal
    scores["csharp"] = sum(
        1 for kw in (".net", "c#", "csharp", "asp.net") if kw in text
    )
    role_family = (
        max(scores, key=lambda k: scores[k]) if any(scores.values()) else "general"
    )
    if scores.get(role_family, 0) == 0:
        role_family = "general"

    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9+.#-]{1,}", text)
    stop = {
        "and",
        "the",
        "with",
        "for",
        "you",
        "will",
        "our",
        "this",
        "that",
        "from",
        "have",
        "are",
        "job",
        "role",
        "team",
        "work",
        "experience",
        "years",
        "using",
        "ability",
        "strong",
        "preferred",
        "required",
        "including",
        "company",
        "location",
        "source",
        "category",
    }
    keywords = []
    seen: set[str] = set()
    for tok in tokens:
        t = tok.lower()
        if t in stop or len(t) < 3:
            continue
        if t not in seen:
            seen.add(t)
            keywords.append(t)
        if len(keywords) >= 40:
            break

    return RequirementSet(
        keywords=keywords,
        role_family=role_family,
        insufficient_description=insufficient,
    )


def job_description_hash(job: JobPosting) -> str:
    return hash_text(job.description_text or "")
