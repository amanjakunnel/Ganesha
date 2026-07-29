from __future__ import annotations

import re

from packages.core.policies.target_companies import TARGET_COMPANY_ALIASES, TARGET_COMPANY_NAMES

_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|plc)\b\.?",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s&]+")


def normalize_company_key(name: str | None) -> str:
    """Normalize a company name for dedupe and referral matching."""
    if not name:
        return ""
    s = name.strip().lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _SUFFIX_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def canonical_company_display(name: str | None) -> str:
    if not name:
        return ""
    key = normalize_company_key(name)
    if key in TARGET_COMPANY_ALIASES:
        return TARGET_COMPANY_ALIASES[key]
    for target in TARGET_COMPANY_NAMES:
        if normalize_company_key(target) == key:
            return target
    return name.strip()


def is_target_company(name: str | None) -> tuple[bool, str | None]:
    """Return (is_target, canonical_target_name)."""
    key = normalize_company_key(name)
    if not key:
        return False, None
    if key in TARGET_COMPANY_ALIASES:
        return True, TARGET_COMPANY_ALIASES[key]
    for target in TARGET_COMPANY_NAMES:
        tkey = normalize_company_key(target)
        if key == tkey or key.startswith(tkey + " ") or tkey in key:
            return True, target
    return False, None


def companies_likely_same(a: str | None, b: str | None) -> bool:
    ka, kb = normalize_company_key(a), normalize_company_key(b)
    if not ka or not kb:
        return False
    return ka == kb or ka in kb or kb in ka
