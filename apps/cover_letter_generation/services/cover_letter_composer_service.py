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
MIN_WORDS = 160
MAX_WORDS = 350


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text))


def _phrase(terms: list[str]) -> str:
    cleaned = [term for term in terms if term]
    if not cleaned:
        return "the posted technical requirements"
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"


def _focus(plan: CoverLetterPlan) -> str:
    terms: list[str] = []
    if plan.primary:
        terms.extend(plan.primary.overlap_terms[:3])
    if not terms:
        terms.append(plan.role_family.replace("_", " "))
    return _phrase(terms)


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
    terms = _phrase(story.overlap_terms[:3]) if story.overlap_terms else story.label
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
        return f"worked on {terms}"
    # Prefer the action clause as written; only reframe the leading verb for grammar.
    if raw[0].isupper() and raw.split()[0].lower() not in {"i", "my", "our"}:
        raw = raw[0].lower() + raw[1:]
    words = raw.split()
    # Short bullets would otherwise paste verbatim; keep the technical gist only.
    if len(source) < 55 or len(words) <= 10:
        return f"handled {terms} work day to day in that role"
    # Longer bullets: keep a partial action fragment, not the full resume line.
    return _clip_words(raw, 14)


def _employer_label(story: StoryEvidence) -> str:
    if story.kind == "project":
        return f"the {story.label} project"
    return f"{story.title} at {story.employer}"


def _metric_clause(story: StoryEvidence) -> str:
    if not story.metric:
        return ""
    return f" One verified result from that work is {story.metric}."


def _story_paragraph(
    story: StoryEvidence, *, role: str, company: str, pattern: int
) -> str:
    clause = _bullet_clause(story)
    where = _employer_label(story)
    metric = _metric_clause(story)
    focus = _phrase(story.overlap_terms[:3]) if story.overlap_terms else "this work"
    if pattern % 3 == 0:
        return (
            f"As {where}, I {clause}.{metric} "
            f"That experience is useful for the {role} role at {company} because "
            f"the posting emphasizes {focus}. In an interview I can walk through "
            f"the systems, tradeoffs, and scope of that work without stretching "
            f"into tools I have not used."
        )
    if pattern % 3 == 1:
        return (
            f"One concrete example is {where}, where I {clause}.{metric} "
            f"For {company}, the connection is the shared need for {focus} rather "
            f"than a generic skills list. The detail I would discuss is how that "
            f"work was operated and maintained, not a restatement of my resume."
        )
    return (
        f"Relevant experience comes from {where}: I {clause}.{metric} "
        f"I would apply the same approach to the {role} responsibilities at "
        f"{company}, staying inside what that posting and my record both support "
        f"and keeping the discussion on {focus}."
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
    focus = _focus(plan)
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
            f"The posting centers on reliable {focus} for people who depend on "
            f"the product, which matches work I have already shipped."
        )
    elif voice == "startup_fast_paced":
        angle = (
            f"The role asks for end-to-end ownership around {focus}, which is "
            f"the part of my background I can discuss with specifics."
        )
    elif voice == "large_company_professional":
        angle = (
            f"The bar described is careful delivery of {focus}, and that is the "
            f"evidence I lead with below."
        )
    elif voice == "mission_oriented":
        angle = (
            f"Where official material supports it, the {focus} work is the "
            f"connection I can make from verified experience."
        )
    else:
        angle = (
            f"I am writing about the {focus} work in the posting, using examples "
            f"I can defend with the record below."
        )
    return (
        f"I am applying for the {plan.role} position at {plan.company}. "
        f"{angle}{company_bit}"
    )


def _closing(plan: CoverLetterPlan, *, used_secondary: bool) -> str:
    focus = _focus(plan)
    if used_secondary and plan.secondary:
        return (
            f"Taken together, those examples cover the {focus} work the "
            f"{plan.role} posting describes at a level I can defend with specifics. "
            f"I would welcome a conversation about how that background applies at "
            f"{plan.company}."
        )
    primary = plan.primary
    where = _employer_label(primary) if primary else "that work"
    return (
        f"I would bring the same approach from {where} to the {plan.role} work at "
        f"{plan.company}, staying with {focus} rather than stretching into tools "
        f"I have not used. I am available to discuss the implementation details "
        f"and the boundaries of that experience."
    )


def compose_deterministic(plan: CoverLetterPlan, *, today: datetime | None = None) -> CoverLetterDocument:
    if plan.primary is None:
        raise ValueError("Cannot compose a letter without primary evidence")
    stamp = today or datetime.now()
    date_line = f"{stamp:%B} {stamp.day}, {stamp:%Y}"
    salutation = f"Dear {plan.company} Hiring Team,"
    seed = int(hashlib.sha1(plan.primary.label.encode()).hexdigest()[:8], 16)
    paragraph_1 = _opening(plan)
    paragraph_2 = _story_paragraph(
        plan.primary, role=plan.role, company=plan.company, pattern=seed
    )
    if plan.secondary:
        paragraph_3 = _story_paragraph(
            plan.secondary,
            role=plan.role,
            company=plan.company,
            pattern=seed + 1,
        )
        # Keep three paragraphs: fold a short close into paragraph 3.
        paragraph_3 = (
            f"{paragraph_3} {_closing(plan, used_secondary=True)}"
        )
    else:
        paragraph_3 = _closing(plan, used_secondary=False)
    paragraphs = [paragraph_1, paragraph_2, paragraph_3]
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
            shared = set(left.split()) & set(right.split())
            if len(shared) >= 22:
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
