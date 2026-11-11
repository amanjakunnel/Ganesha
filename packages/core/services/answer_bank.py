"""Local approved answer bank (gitignored path under ~/.ganesha/).

Never infer answers. Sensitive categories require an explicitly approved entry
for the requested scope. Contents must not be logged or sent to LLMs.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

Scope = Literal["global", "company"]

ANSWER_BANK_DIR = Path.home() / ".ganesha"
ANSWER_BANK_PATH = ANSWER_BANK_DIR / "answer_bank.json"

SENSITIVE_CATEGORIES: tuple[str, ...] = (
    "work_authorization",
    "sponsorship",
    "citizenship",
    "clearance",
    "salary",
    "relocation",
    "start_date",
    "demographic",
    "disability",
    "legal_declaration",
    "consent",
)

SENSITIVE_QUESTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("work_authorization", re.compile(r"work\s+authori[sz]ation|authori[sz]ed\s+to\s+work|legally\s+authori[sz]ed", re.I)),
    ("sponsorship", re.compile(r"sponsor(ship)?|visa\b|h-?1b", re.I)),
    ("citizenship", re.compile(r"citizen(ship)?", re.I)),
    ("clearance", re.compile(r"clearance|security\s+clearance", re.I)),
    ("salary", re.compile(r"salary|compensation|pay\s+expect", re.I)),
    ("relocation", re.compile(r"relocat", re.I)),
    ("start_date", re.compile(r"start\s+date|available\s+to\s+start", re.I)),
    ("demographic", re.compile(r"gender|race|ethnicity|veteran|demographic", re.I)),
    ("disability", re.compile(r"disability", re.I)),
    ("legal_declaration", re.compile(r"certify|acknowledge|legal\s+declaration", re.I)),
    ("consent", re.compile(r"\bconsent\b|terms\s+and\s+conditions|privacy\s+policy", re.I)),
)


@dataclass
class AnswerBankEntry:
    id: str
    question_pattern: str
    answer: str
    scope: Scope
    company: str | None
    category: str | None
    approved_at: str
    sensitive: bool

    def to_public_dict(self) -> dict[str, Any]:
        """Dashboard listing — includes answer only for this dedicated UI."""
        return asdict(self)


def classify_sensitive(question: str) -> str | None:
    for category, pattern in SENSITIVE_QUESTION_PATTERNS:
        if pattern.search(question or ""):
            return category
    return None


def _load_raw(path: Path = ANSWER_BANK_PATH) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save_raw(entries: list[dict[str, Any]], path: Path = ANSWER_BANK_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def list_entries(*, path: Path = ANSWER_BANK_PATH) -> list[AnswerBankEntry]:
    out: list[AnswerBankEntry] = []
    for raw in _load_raw(path):
        try:
            out.append(
                AnswerBankEntry(
                    id=str(raw["id"]),
                    question_pattern=str(raw["question_pattern"]),
                    answer=str(raw["answer"]),
                    scope=raw.get("scope") or "global",
                    company=raw.get("company"),
                    category=raw.get("category"),
                    approved_at=str(raw.get("approved_at") or ""),
                    sensitive=bool(raw.get("sensitive")),
                )
            )
        except (KeyError, TypeError):
            continue
    return out


def add_entry(
    *,
    question_pattern: str,
    answer: str,
    scope: Scope = "global",
    company: str | None = None,
    category: str | None = None,
    path: Path = ANSWER_BANK_PATH,
) -> AnswerBankEntry:
    if not question_pattern.strip() or not answer.strip():
        raise ValueError("question_pattern and answer are required")
    if scope == "company" and not (company or "").strip():
        raise ValueError("company is required for company scope")
    sensitive_cat = category or classify_sensitive(question_pattern)
    entry = AnswerBankEntry(
        id=str(uuid.uuid4()),
        question_pattern=question_pattern.strip(),
        answer=answer.strip(),
        scope=scope,
        company=(company or None),
        category=sensitive_cat,
        approved_at=datetime.now(UTC).isoformat(),
        sensitive=sensitive_cat in SENSITIVE_CATEGORIES if sensitive_cat else False,
    )
    rows = _load_raw(path)
    rows.append(asdict(entry))
    _save_raw(rows, path)
    return entry


def update_entry(
    entry_id: str,
    *,
    question_pattern: str | None = None,
    answer: str | None = None,
    scope: Scope | None = None,
    company: str | None = None,
    path: Path = ANSWER_BANK_PATH,
) -> AnswerBankEntry | None:
    rows = _load_raw(path)
    for raw in rows:
        if str(raw.get("id")) != entry_id:
            continue
        if question_pattern is not None:
            raw["question_pattern"] = question_pattern.strip()
        if answer is not None:
            raw["answer"] = answer.strip()
        if scope is not None:
            raw["scope"] = scope
        if company is not None:
            raw["company"] = company
        raw["approved_at"] = datetime.now(UTC).isoformat()
        cat = classify_sensitive(str(raw.get("question_pattern") or ""))
        raw["category"] = cat
        raw["sensitive"] = cat in SENSITIVE_CATEGORIES if cat else False
        _save_raw(rows, path)
        return AnswerBankEntry(**{k: raw[k] for k in AnswerBankEntry.__dataclass_fields__})
    return None


def delete_entry(entry_id: str, *, path: Path = ANSWER_BANK_PATH) -> bool:
    rows = _load_raw(path)
    next_rows = [r for r in rows if str(r.get("id")) != entry_id]
    if len(next_rows) == len(rows):
        return False
    _save_raw(next_rows, path)
    return True


def lookup_answer(
    question: str,
    *,
    company: str | None = None,
    path: Path = ANSWER_BANK_PATH,
) -> AnswerBankEntry | None:
    """Return approved answer for question. Sensitive → requires matching scope entry."""
    category = classify_sensitive(question)
    entries = list_entries(path=path)
    company_norm = (company or "").strip().lower()
    # Prefer company-scoped matches, then global.
    ordered = sorted(
        entries,
        key=lambda e: 0 if e.scope == "company" else 1,
    )
    for entry in ordered:
        if entry.scope == "company":
            if not company_norm or (entry.company or "").strip().lower() != company_norm:
                continue
        try:
            if not re.search(entry.question_pattern, question or "", flags=re.I):
                continue
        except re.error:
            if entry.question_pattern.lower() not in (question or "").lower():
                continue
        if category and entry.sensitive and entry.category != category:
            # Wrong sensitive category — do not fill.
            continue
        if category and not entry.sensitive and entry.category != category:
            # Sensitive question requires an approved sensitive entry.
            continue
        if category and not entry.sensitive:
            continue
        return entry
    return None


def resolve_for_autofill(
    question: str,
    *,
    company: str | None = None,
    path: Path = ANSWER_BANK_PATH,
) -> dict[str, Any]:
    """Autofill resolution. Never invents answers."""
    category = classify_sensitive(question)
    entry = lookup_answer(question, company=company, path=path)
    if entry is not None:
        return {
            "status": "filled_from_bank",
            "answer": entry.answer,
            "entry_id": entry.id,
            "category": category,
            "sensitive": bool(category),
        }
    if category:
        return {
            "status": "needs_input",
            "answer": None,
            "entry_id": None,
            "category": category,
            "sensitive": True,
            "offer_add_to_bank": True,
        }
    return {
        "status": "needs_input",
        "answer": None,
        "entry_id": None,
        "category": None,
        "sensitive": False,
        "offer_add_to_bank": True,
    }
