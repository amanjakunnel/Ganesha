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

_GENERIC = (
    "i am excited to apply",
    "this aligns perfectly",
    "i am thrilled",
    "perfect fit",
    "passionate about innovation",
    "to whom it may concern",
)
_FORBIDDEN = re.compile(
    r"\b(work authorization|f-1|opt|stem opt|sponsorship|citizenship|visa|"
    r"immigration|referral)\b",
    re.I,
)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

_VOICE_LINE = {
    "technical_direct": "The technical requirement I am responding to is {focus}.",
    "product_oriented": (
        "The product outcome I am responding to is reliable {focus} for the people "
        "who depend on it."
    ),
    "mission_oriented": (
        "Where official material supports it, the {focus} work is the part of "
        "{company} I can connect to verified experience."
    ),
    "startup_fast_paced": (
        "The ownership this role describes is end-to-end work on {focus}."
    ),
    "large_company_professional": (
        "The operating bar this role describes is disciplined delivery of {focus}."
    ),
}


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text))


def _phrase(terms: list[str]) -> str:
    cleaned = [term for term in terms if term]
    if not cleaned:
        return "the posted requirements"
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


def _story_sentence(story: StoryEvidence, *, role: str, company: str, lead: str) -> str:
    terms = _phrase(story.overlap_terms[:3]) if story.overlap_terms else story.label
    metric = ""
    if story.metric:
        metric = f" A verified figure from that work is {story.metric}."
    subject = (
        f"the {story.label} project"
        if story.kind == "project"
        else f"my work as {story.title} at {story.employer}"
    )
    return (
        f"{lead} {subject}. The verified work involved {terms}.{metric} "
        f"That is relevant to the {role} role at {company} because the posting "
        f"asks for those capabilities, and I am not treating other tools in the "
        f"description as experience I already have."
    )


def _company_sentence(plan: CoverLetterPlan) -> str:
    usable = [item for item in plan.company_evidence if item.usable]
    if not usable:
        return (
            f"This letter uses the {plan.role} description and verified experience "
            f"only."
        )
    item = usable[0]
    excerpt = " ".join(item.excerpt.split()[:12])
    excerpt = re.sub(r"https?://\S+|www\.\S+", "", excerpt).strip()
    return (
        f"An official {item.source_type.replace('_', ' ')} page for {plan.company} "
        f"is the company context used here: {excerpt}."
    )


def _voice_sentence(plan: CoverLetterPlan) -> str:
    voice = plan.voice if plan.voice in _VOICE_LINE else DEFAULT_VOICE
    if voice == "mission_oriented" and not plan.company_evidence:
        voice = "technical_direct"
    template = _VOICE_LINE[voice]
    return template.format(focus=_focus(plan), company=plan.company)


def compose_deterministic(plan: CoverLetterPlan, *, today: datetime | None = None) -> CoverLetterDocument:
    if plan.primary is None:
        raise ValueError("Cannot compose a letter without primary evidence")
    stamp = today or datetime.now()
    date_line = f"{stamp:%B} {stamp.day}, {stamp:%Y}"
    salutation = f"Dear {plan.company} Hiring Team,"
    focus = _focus(plan)
    paragraph_1 = (
        f"I am applying for the {plan.role} position at {plan.company}. "
        f"{_voice_sentence(plan)} "
        f"The description's emphasis on {focus} matches work I have already done, "
        f"which is the reason this role is a concrete fit. {_company_sentence(plan)}"
    )
    paragraph_2 = _story_sentence(
        plan.primary,
        role=plan.role,
        company=plan.company,
        lead="The strongest related example is",
    )
    if plan.secondary:
        paragraph_3 = _story_sentence(
            plan.secondary,
            role=plan.role,
            company=plan.company,
            lead="A complementary example is",
        )
        paragraph_3 += (
            f" Together, those two examples are the scope of what I would bring "
            f"to {plan.company}."
        )
    else:
        paragraph_3 = (
            f"The same {plan.primary.label} evidence is the contribution I would "
            f"bring to {plan.company}: experience with {focus}, applied carefully "
            f"to the {plan.role} work described in the posting. "
            f"I would welcome a conversation about that fit."
        )
    paragraphs = _fit_length([paragraph_1, paragraph_2, paragraph_3], plan)
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


def _fit_length(paragraphs: list[str], plan: CoverLetterPlan) -> list[str]:
    """Add explanation from already selected terms until the letter is concise."""
    focus = _focus(plan)
    extras = [
        (
            f"I would use that {focus} experience to contribute quickly to the "
            f"{plan.role} work at {plan.company}, and only where the posting and "
            f"the verified role use the same terms."
        ),
        (
            f"The letter stays with those examples so the claim is limited to "
            f"verified work at "
            f"{plan.primary.employer if plan.primary else plan.company}."
        ),
        "I would welcome a short conversation about how that evidence applies.",
    ]
    updated = list(paragraphs)
    extra_index = 0
    while word_count(" ".join(updated)) < 250 and extra_index < len(extras):
        updated[2] = updated[2].rstrip() + " " + extras[extra_index]
        extra_index += 1
    return updated


def resume_bullet_overlap(paragraphs: list[str], bullets: list[str]) -> list[str]:
    text = " ".join(paragraphs).lower()
    hits: list[str] = []
    for bullet in bullets:
        cleaned = " ".join(bullet.split())
        if len(cleaned) >= 40 and cleaned.lower() in text:
            hits.append(cleaned)
    return hits


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
    if count < 225 or count > 375:
        problems.append(f"Word count {count} is outside 225-375")
    if len(document.paragraphs) != 3:
        problems.append("Letter must contain three paragraphs")
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
            "Use only the provided facts. Do not invent metrics, employers, or "
            "company claims."
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
