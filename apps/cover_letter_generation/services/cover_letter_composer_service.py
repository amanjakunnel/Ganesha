from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from apps.cover_letter_generation.paths import (
    CACHE_DIR,
    CANDIDATE_NAME,
    DEFAULT_VOICE,
    PROMPT_VERSION,
)
from apps.cover_letter_generation.schemas import (
    CompanyEvidence,
    CoverLetterDocument,
    CoverLetterPlan,
    StoryEvidence,
)
from packages.core.settings import settings

# Hard blockers: fluff or house boilerplate that replaces substance.
_GENERIC = (
    "i am excited to apply",
    "this aligns perfectly",
    "i am thrilled",
    "perfect fit",
    "passionate about innovation",
    "to whom it may concern",
    "concrete fit",
    "the verified work involved",
    "the strongest related example is",
    "a complementary example is",
    "i am not treating other tools",
    "i have always been passionate",
    "cutting-edge",
    "seamless",
    "leverage my passion",
    "the posting emphasizes",
    "i am writing about the",
    "shared need for",
    "overlap on",
    "posting ask",
    "posting asks for this",
    "this posting ask",
    "asks for this:",
    "responsibility that stands out is:",
    "verified result of",
)
_FORBIDDEN = re.compile(
    r"\b(work authorization|f-1|opt|stem opt|sponsorship|citizenship|visa|"
    r"immigration|referral)\b",
    re.I,
)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_INFLATED = re.compile(
    r"\b(spearheaded|architected|revolutionized|disrupted|synerg(?:y|ized))\b",
    re.I,
)

# Soft style signals — warn, do not block release.
_STYLE_WARN = (
    "leverage",
    "cutting-edge",
    "seamless",
    "robust",
    "innovative",
    "impactful",
    "fast-paced environment",
)

# Prefer shorter concrete letters over padded filler (documented migration from 225).
MIN_WORDS = 140
MAX_WORDS = 350

_TECH_CAPS = {
    "aws": "AWS",
    "azure": "Azure",
    "gcp": "GCP",
    "kafka": "Kafka",
    "kubernetes": "Kubernetes",
    "k8s": "Kubernetes",
    "docker": "Docker",
    "jenkins": "Jenkins",
    "terraform": "Terraform",
    "ansible": "Ansible",
    "python": "Python",
    "java": "Java",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "sql": "SQL",
    "api": "API",
    "apis": "APIs",
    "rest": "REST",
    "ci/cd": "CI/CD",
    "mern": "MERN",
    "pytorch": "PyTorch",
    "mongodb": "MongoDB",
    "rabbitmq": "RabbitMQ",
}


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text))


def capitalize_technologies(text: str) -> str:
    """Normalize known technology names without rewriting surrounding prose."""

    def repl(match: re.Match[str]) -> str:
        raw = match.group(0)
        key = raw.lower()
        return _TECH_CAPS.get(key, raw)

    pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in sorted(_TECH_CAPS, key=len, reverse=True)) + r")\b",
        re.I,
    )
    return pattern.sub(repl, text)


_VERB_TO_GERUND = {
    "own": "owning",
    "lead": "leading",
    "design": "designing",
    "develop": "developing",
    "build": "building",
    "implement": "implementing",
    "maintain": "maintaining",
    "mentor": "mentoring",
    "troubleshoot": "troubleshooting",
    "investigate": "investigating",
    "collaborate": "collaborating",
    "deliver": "delivering",
    "deploy": "deploying",
    "monitor": "monitoring",
    "apply": "applying",
    "conduct": "conducting",
    "define": "defining",
    "resolve": "resolving",
}


def _natural_responsibility(story: StoryEvidence | None, plan: CoverLetterPlan) -> str:
    """Brief gerund/noun phrase from a JD responsibility — not a colon-pasted dump."""
    raw = ""
    if story and story.jd_responsibility:
        raw = story.jd_responsibility.strip().rstrip(".")
    if not raw:
        family = plan.role_family.replace("_", " ")
        return f"reliable {family} delivery"
    words = raw.split()
    lead = re.sub(r"[^a-zA-Z]+$", "", words[0])
    first = lead.lower()
    if first in _VERB_TO_GERUND:
        words[0] = _VERB_TO_GERUND[first] + words[0][len(lead) :]
    joined = " ".join(words)
    # "investigating, troubleshoot, and resolve X" → "investigating X"
    gerunds = "|".join(
        re.escape(g) for g in sorted(_VERB_TO_GERUND.values(), key=len, reverse=True)
    )
    joined = re.sub(
        rf"^({gerunds})(?:,\s*[A-Za-z]+)*,\s+and\s+[A-Za-z]+\s+",
        r"\1 ",
        joined,
        count=1,
        flags=re.I,
    )
    joined = re.sub(
        rf"^({gerunds})\s+and\s+[A-Za-z]+\s+",
        r"\1 ",
        joined,
        count=1,
        flags=re.I,
    )
    # Keep the responsibility readable; drop trailing clauses that read as JD paste.
    clipped = _clip_words(joined, 12)
    return clipped[0].lower() + clipped[1:] if clipped else clipped


def _clip_words(text: str, limit: int = 22) -> str:
    words = text.split()
    if len(words) <= limit:
        return text
    chunk = " ".join(words[:limit]).rstrip(",;:")
    # Prefer ending on a natural boundary so we do not leave "and"/"across" dangling.
    for sep in (", ", "; ", " and ", " with ", " through ", " across "):
        idx = chunk.rfind(sep)
        if idx >= 40:
            return chunk[:idx].rstrip(",;:")
    # Drop a trailing dangling conjunction/preposition.
    while chunk.split() and chunk.split()[-1].lower() in {
        "and",
        "or",
        "with",
        "to",
        "for",
        "of",
        "on",
        "in",
        "across",
        "through",
        "including",
    }:
        chunk = " ".join(chunk.split()[:-1])
    return chunk.rstrip(",;:")


def _bullet_clause(story: StoryEvidence) -> str:
    """Turn a verified bullet into a first-person clause without inventing detail.

    Uses a shortened verified fragment so the letter explains relevance instead of
    pasting the resume bullet verbatim.
    """
    raw = ""
    source = ""
    for bullet in story.source_bullets:
        cleaned = " ".join(bullet.split()).strip().rstrip(".")
        if len(cleaned) >= 24:
            raw = cleaned
            source = cleaned
            break
    if not raw and story.source_bullets:
        raw = " ".join(story.source_bullets[0].split()).strip().rstrip(".")
        source = raw
    if not raw:
        return "did related engineering work I can discuss from that role"
    # Prefer the action clause as written; only reframe the leading verb for grammar.
    if raw[0].isupper() and raw.split()[0].lower() not in {"i", "my", "our"}:
        raw = raw[0].lower() + raw[1:]
    words = raw.split()
    # Short bullets reproduce verbatim too easily; keep only a verb-led stub.
    if len(source) < 60 or len(words) <= 10:
        stub = " ".join(words[:5]).rstrip(",;:")
        if len(stub) >= 40 and stub.lower() in source.lower():
            stub = " ".join(words[:3]).rstrip(",;:")
        return stub or "did related engineering work in that role"
    clipped = _clip_words(raw, 14)
    # Guard against recreating a full resume bullet (>=40 chars).
    if len(source) >= 40 and source.lower() in f"i {clipped}".lower():
        clipped = _clip_words(raw, 7)
    return clipped or "did related engineering work in that role"


def _employer_label(story: StoryEvidence) -> str:
    if story.kind == "project":
        return f"the {story.label} project"
    return f"{story.title} at {story.employer}"


def _metric_clause(story: StoryEvidence) -> str:
    if not story.metric:
        return ""
    # Keep the verified figure; do not invent what the number measured.
    return f" A recorded outcome from that work was {story.metric}."


def _story_paragraph(
    story: StoryEvidence,
    *,
    plan: CoverLetterPlan,
    pattern: int,
) -> str:
    clause = _bullet_clause(story)
    where = _employer_label(story)
    metric = _metric_clause(story)
    ask = _natural_responsibility(story, plan)
    role = plan.role
    company = plan.company
    if story.connection == "weak":
        return (
            f"The closest verified example is {where}, where I {clause}.{metric} "
            f"It only partially covers {ask} for {company}, so I would keep that "
            f"claim bounded in an interview rather than stretch it. The value is "
            f"the concrete systems I can describe, not a stronger ownership claim."
        )
    if pattern % 3 == 0:
        return (
            f"As {where}, I {clause}.{metric} "
            f"That experience is useful for {role} work at {company} because it "
            f"already involved production systems adjacent to {ask}. I can walk "
            f"through those systems and tradeoffs in an interview without claiming "
            f"tools or outcomes outside the record."
        )
    if pattern % 3 == 1:
        return (
            f"One concrete example is {where}, where I {clause}.{metric} "
            f"For {company}, that matters for {ask}: the same production habits "
            f"are what I can discuss with specifics rather than a restated resume "
            f"line. I stay with what that role actually covered."
        )
    return (
        f"Relevant experience comes from {where}: I {clause}.{metric} "
        f"I would bring that same operating approach to {ask} in the {role} role "
        f"at {company}, staying inside what both the posting and my record support "
        f"and leaving room to discuss the boundaries of that experience."
    )


def _company_sentence(plan: CoverLetterPlan) -> str:
    usable = [item for item in plan.company_evidence if item.usable]
    if not usable:
        return ""
    item = usable[0]
    excerpt = " ".join(item.excerpt.split()[:14])
    excerpt = re.sub(r"https?://\S+|www\.\S+", "", excerpt).strip().rstrip(".,;")
    if not excerpt:
        return ""
    return (
        f" Official {plan.company} material describes {excerpt}, which is the "
        f"company context used here—not a claim about personal motivation."
    )


def _opening(plan: CoverLetterPlan) -> str:
    ask = _natural_responsibility(plan.primary, plan)
    voice = plan.voice if plan.voice in {
        "technical_direct",
        "product_oriented",
        "mission_oriented",
        "startup_fast_paced",
        "large_company_professional",
    } else DEFAULT_VOICE
    if voice == "mission_oriented" and not plan.company_evidence:
        voice = "technical_direct"
    company_bit = _company_sentence(plan)
    if voice == "product_oriented":
        angle = (
            f"The product-facing need I can address is {ask}. "
            f"I answer that with verified work below, not with generic praise."
        )
    elif voice == "startup_fast_paced":
        angle = (
            f"The ownership need I can speak to is {ask}. "
            f"That is the part of my background I can discuss with specifics."
        )
    elif voice == "large_company_professional":
        angle = (
            f"The operating need I lead with is {ask}. "
            f"The examples below stay inside verified evidence for that work."
        )
    elif voice == "mission_oriented":
        angle = (
            f"Where official material supports it, I can connect verified "
            f"experience to {ask}."
        )
    else:
        angle = (
            f"A core need in the role is {ask}. "
            f"Below I connect verified work to that responsibility."
        )
    return (
        f"I am applying for the {plan.role} position at {plan.company}. "
        f"{angle}{company_bit}"
    )


def _closing(plan: CoverLetterPlan, *, used_secondary: bool) -> str:
    if used_secondary and plan.secondary:
        return (
            f"Taken together, those examples stay inside verified work. "
            f"I would welcome a conversation about how that background applies at "
            f"{plan.company}."
        )
    primary = plan.primary
    where = _employer_label(primary) if primary else "that work"
    ask = _natural_responsibility(primary, plan)
    return (
        f"I would bring the same approach from {where} to {ask} in the "
        f"{plan.role} role at {plan.company}, without stretching into tools I "
        f"have not used. I am available to discuss the boundaries of that "
        f"experience."
    )


def compose_deterministic(plan: CoverLetterPlan, *, today: datetime | None = None) -> CoverLetterDocument:
    if plan.primary is None:
        raise ValueError("Cannot compose a letter without primary evidence")
    stamp = today or datetime.now()
    date_line = f"{stamp:%B} {stamp.day}, {stamp:%Y}"
    salutation = f"Dear {plan.company} Hiring Team,"
    seed = int(hashlib.sha1(plan.primary.label.encode()).hexdigest()[:8], 16)
    paragraph_1 = _opening(plan)
    paragraph_2 = _story_paragraph(plan.primary, plan=plan, pattern=seed)
    if plan.secondary:
        paragraph_3 = _story_paragraph(
            plan.secondary,
            plan=plan,
            pattern=seed + 1,
        )
        # Keep three paragraphs: fold a short close into paragraph 3.
        paragraph_3 = (
            f"{paragraph_3} {_closing(plan, used_secondary=True)}"
        )
    else:
        paragraph_3 = _closing(plan, used_secondary=False)
    paragraphs = [
        capitalize_technologies(part) for part in (paragraph_1, paragraph_2, paragraph_3)
    ]
    fact_ids = list(plan.primary.fact_ids)
    if plan.secondary:
        fact_ids.extend(plan.secondary.fact_ids)
    return CoverLetterDocument(
        name_line=CANDIDATE_NAME,
        contact_line=plan.contact_line,
        date_line=date_line,
        salutation=salutation,
        paragraphs=paragraphs,
        company=plan.company,
        role=plan.role,
        voice=plan.voice,
        fact_ids=fact_ids,
        company_fact_ids=[item.evidence_id for item in plan.company_evidence if item.usable],
    )


def resume_bullet_overlap(paragraphs: list[str], bullets: list[str]) -> list[str]:
    text = " ".join(paragraphs).lower()
    hits: list[str] = []
    for bullet in bullets:
        cleaned = " ".join(bullet.split())
        if len(cleaned) >= 40 and cleaned.lower() in text:
            hits.append(cleaned)
    return hits


def style_warnings(document: CoverLetterDocument) -> list[str]:
    """Non-blocking writing signals for diagnostics."""
    warnings: list[str] = []
    body = "\n".join(document.paragraphs)
    lower = body.lower()
    for phrase in _STYLE_WARN:
        if phrase in lower:
            warnings.append(f"Style caution: '{phrase}'")
    openings = [p.split()[:4] for p in document.paragraphs if p.split()]
    if len(openings) >= 2:
        heads = [" ".join(o).lower() for o in openings]
        if len(set(heads)) < len(heads):
            warnings.append("Style caution: repeated paragraph openings")
    norms = [" ".join(p.lower().split()) for p in document.paragraphs]
    for i, left in enumerate(norms):
        for right in norms[i + 1 :]:
            left_words = left.split()
            right_words = right.split()
            shared = set(left_words) & set(right_words)
            denom = max(len(left_words), len(right_words), 1)
            # Shared responsibility phrases are expected; warn only on near-copies.
            if len(shared) / denom >= 0.55 and denom >= 35:
                warnings.append("Style caution: paragraphs share substantial wording")
                break
    if _INFLATED.search(body):
        warnings.append("Style caution: inflated ownership verb")
    # Unexplained skill dumps: long comma lists with no action verbs nearby.
    for paragraph in document.paragraphs:
        if paragraph.count(",") >= 6 and not re.search(
            r"\b(built|designed|owned|implemented|deployed|debugged|wrote|"
            r"maintained|collaborated|handled|applying|writing)\b",
            paragraph,
            re.I,
        ):
            warnings.append("Style caution: unexplained skill list")
            break
    count = word_count(body)
    if count > 325:
        warnings.append(f"Style caution: letter is long ({count} words)")
    return warnings


def prose_problems(
    document: CoverLetterDocument,
    plan: CoverLetterPlan,
    *,
    allowed_numbers: set[str],
) -> list[str]:
    problems: list[str] = []
    body = "\n".join(document.paragraphs)
    lower = body.lower()
    if document.company.lower() not in lower or document.role.lower() not in lower:
        problems.append("Role or company missing from the letter body")
    if not document.salutation.startswith(f"Dear {document.company} Hiring Team"):
        problems.append("Salutation is not the company hiring team")
    for phrase in _GENERIC:
        if phrase in lower or phrase in document.salutation.lower():
            problems.append(f"Generic wording: {phrase}")
    if _FORBIDDEN.search(body) or _FORBIDDEN.search(document.salutation):
        problems.append("Disallowed authorization or referral language")
    if "http://" in lower or "https://" in lower or "www." in lower:
        problems.append("Raw URL in letter body")
    overlap = resume_bullet_overlap(
        document.paragraphs,
        (plan.primary.source_bullets if plan.primary else [])
        + (plan.secondary.source_bullets if plan.secondary else []),
    )
    if overlap:
        problems.append("Letter repeats a resume bullet verbatim")
    for number in _NUMBER_RE.findall(body):
        if number not in allowed_numbers:
            problems.append(f"Unsupported number in letter: {number}")
    count = word_count(body)
    if count < MIN_WORDS or count > MAX_WORDS:
        problems.append(f"Word count {count} is outside {MIN_WORDS}-{MAX_WORDS}")
    if len(document.paragraphs) != 3:
        problems.append("Letter must contain three paragraphs")
    # Exact or near-exact duplicate paragraphs are factual/structural failures.
    norms = [" ".join(p.lower().split()) for p in document.paragraphs]
    for i, left in enumerate(norms):
        for right in norms[i + 1 :]:
            if not left or not right:
                continue
            if left == right:
                problems.append("Duplicate paragraphs")
                break
            left_words = left.split()
            right_words = right.split()
            shared = set(left_words) & set(right_words)
            denom = max(len(left_words), len(right_words), 1)
            if len(shared) / denom >= 0.85 and denom >= 25:
                problems.append("Near-duplicate paragraphs")
                break
    return problems


def allowed_numbers(plan: CoverLetterPlan, job_description: str) -> set[str]:
    blobs = [job_description]
    for story in (plan.primary, plan.secondary):
        if story:
            blobs.extend(story.source_bullets)
            if story.metric:
                blobs.append(story.metric)
    for item in plan.company_evidence:
        blobs.append(item.excerpt)
    return set(_NUMBER_RE.findall(" ".join(blobs)))


def _cache_path(plan: CoverLetterPlan, model: str) -> Path:
    fact_ids: list[str] = []
    if plan.primary:
        fact_ids.extend(plan.primary.fact_ids)
    if plan.secondary:
        fact_ids.extend(plan.secondary.fact_ids)
    company_ids = [item.evidence_id for item in plan.company_evidence]
    payload = "|".join(
        [
            plan.job_id,
            plan.voice,
            ",".join(sorted(fact_ids)),
            ",".join(sorted(company_ids)),
            model,
            PROMPT_VERSION,
        ]
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def validate_model_payload(
    data: dict[str, object],
    plan: CoverLetterPlan,
    *,
    allowed_numbers_set: set[str],
) -> CoverLetterDocument | None:
    """Accept model prose only when every claim stays inside the selected evidence."""
    selected = set(plan.primary.fact_ids if plan.primary else [])
    if plan.secondary:
        selected.update(plan.secondary.fact_ids)
    claim_ids = data.get("factual_claim_ids")
    if not isinstance(claim_ids, list) or not claim_ids:
        return None
    if any(not isinstance(item, str) or item not in selected for item in claim_ids):
        return None
    company_ids = data.get("company_fact_ids") or []
    approved = {item.evidence_id for item in plan.company_evidence if item.usable}
    if not isinstance(company_ids, list):
        return None
    if any(not isinstance(item, str) or item not in approved for item in company_ids):
        return None
    paragraphs = [data.get("paragraph_1"), data.get("paragraph_2"), data.get("paragraph_3")]
    if any(not isinstance(paragraph, str) or not paragraph.strip() for paragraph in paragraphs):
        return None
    text_paragraphs = [str(paragraph).strip() for paragraph in paragraphs]
    document = CoverLetterDocument(
        name_line=CANDIDATE_NAME,
        contact_line=plan.contact_line,
        date_line="",
        salutation=f"Dear {plan.company} Hiring Team,",
        paragraphs=text_paragraphs,
        company=plan.company,
        role=plan.role,
        voice=plan.voice,
        fact_ids=[str(item) for item in claim_ids],
        company_fact_ids=[str(item) for item in company_ids],
    )
    if prose_problems(document, plan, allowed_numbers=allowed_numbers_set):
        return None
    return document


def try_model_prose(
    plan: CoverLetterPlan,
    *,
    job_description: str,
    deterministic_only: bool,
    client: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> CoverLetterDocument | None:
    if deterministic_only:
        return None
    provider = (settings.semantic_fit_provider or "none").strip().lower()
    if provider != "anthropic" or not (settings.anthropic_api_key or "").strip():
        return None
    if plan.primary is None:
        return None
    numbers = allowed_numbers(plan, job_description)
    cache = _cache_path(plan, settings.anthropic_model)
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if isinstance(cached, dict):
            validated = validate_model_payload(cached, plan, allowed_numbers_set=numbers)
            if validated:
                return validated
    company_payload: list[dict[str, str]] = [
        {
            "id": item.evidence_id,
            "source_type": item.source_type,
            "excerpt": item.excerpt,
        }
        for item in plan.company_evidence
        if item.usable
    ]
    request = {
        "task": (
            "Write three cover-letter paragraphs as JSON keys paragraph_1, "
            "paragraph_2, paragraph_3, factual_claim_ids, company_fact_ids, warnings. "
            "Use only the provided facts. Do not invent metrics, employers, "
            "motivations, or company claims. Prefer plain specific wording over "
            "generic praise. Explain why the selected work matters for this role; "
            "do not paste resume bullets. About 180-325 words total."
        ),
        "voice": plan.voice,
        "role": plan.role,
        "company": plan.company,
        "facts": _story_payload(plan.primary) + _story_payload(plan.secondary),
        "company_facts": company_payload,
    }
    caller = client or _default_model_client
    try:
        parsed = caller(request)
    except Exception:
        return None
    validated = validate_model_payload(parsed, plan, allowed_numbers_set=numbers)
    if validated is None:
        return None
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(parsed), encoding="utf-8")
    return validated


def _story_payload(story: StoryEvidence | None) -> list[dict[str, object]]:
    if story is None:
        return []
    return [
        {
            "fact_ids": story.fact_ids,
            "label": story.label,
            "bullets": story.source_bullets,
            "metric": story.metric,
        }
    ]


def _default_model_client(request: dict[str, Any]) -> dict[str, Any]:
    import urllib.request

    body = json.dumps(
        {
            "model": settings.anthropic_model,
            "max_tokens": 900,
            "messages": [{"role": "user", "content": json.dumps(request)}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": settings.anthropic_api_key or "",
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:  # noqa: S310
        raw = json.loads(resp.read().decode("utf-8"))
    text = "\n".join(
        block.get("text") or ""
        for block in raw.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text"
    )
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Cover-letter model returned non-JSON content")
    parsed_json = json.loads(text[start : end + 1])
    if not isinstance(parsed_json, dict):
        raise ValueError("Cover-letter model JSON was not an object")
    return parsed_json


def unused_company_evidence(plan: CoverLetterPlan) -> list[CompanyEvidence]:
    return [item for item in plan.company_evidence if not item.usable]
