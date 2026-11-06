"""Writing-quality diagnostics for resumes and cover letters.

Factual / structural failures continue to live in existing validators.
These checks produce non-blocking style warnings (and a few hard checks
shared with cover-letter prose_problems) so generation stays bounded.
"""

from __future__ import annotations

import re
from collections import Counter

from apps.resume_generation.schemas import ResumeDocument

_INFLATED_VERBS = re.compile(
    r"^(architected|spearheaded|revolutionized|orchestrated|disrupted)\b",
    re.I,
)
_GENERIC_FILLER = re.compile(
    r"\b(leveraged|cutting-edge|seamless|robust|innovative|impactful|"
    r"passion(?:ate)? for|perfect fit)\b",
    re.I,
)


def _opening_verb(text: str) -> str:
    first = (text.split() or [""])[0].lower().strip(".,;:")
    return first


def resume_style_warnings(document: ResumeDocument) -> list[str]:
    """Non-blocking style signals for resume composition diagnostics."""
    warnings: list[str] = []
    bullets = [
        bullet.text.strip()
        for entry in (*document.experience, *document.projects)
        for bullet in entry.bullets
        if bullet.text.strip()
    ]
    openings = [_opening_verb(b) for b in bullets]
    counts = Counter(openings)
    repeated = [verb for verb, count in counts.items() if verb and count >= 3]
    if repeated:
        warnings.append(
            "Style caution: repeated bullet openings ("
            + ", ".join(sorted(repeated)[:4])
            + ")"
        )
    inflated = [b for b in bullets if _INFLATED_VERBS.search(b)]
    if inflated:
        warnings.append(
            f"Style caution: {len(inflated)} bullet(s) use strong ownership verbs; "
            "confirm they match verified scope"
        )
    filler_hits = sum(1 for b in bullets if _GENERIC_FILLER.search(b))
    if filler_hits:
        warnings.append(
            f"Style caution: {filler_hits} bullet(s) contain generic filler wording"
        )
    # Near-duplicate openings across roles (first 6 tokens).
    heads = [" ".join(b.lower().split()[:6]) for b in bullets]
    if len(heads) != len(set(heads)):
        warnings.append("Style caution: near-duplicate bullet openings")
    return warnings


def resume_hard_writing_problems(document: ResumeDocument) -> list[str]:
    """Blocking writing problems (kept separate from ATS layout checks)."""
    problems: list[str] = []
    seen: set[str] = set()
    for entry in (*document.experience, *document.projects):
        for bullet in entry.bullets:
            norm = " ".join(bullet.text.lower().split())
            if not norm:
                continue
            if norm in seen:
                problems.append("Duplicate resume bullet text")
            seen.add(norm)
    return problems
