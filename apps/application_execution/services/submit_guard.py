"""Code-level denylist so controlled browsers never click final Submit."""

from __future__ import annotations

import re

SUBMIT_DENYLIST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*submit\s*$", re.IGNORECASE),
    re.compile(r"submit\s+application", re.IGNORECASE),
    re.compile(r"submit\s+your\s+application", re.IGNORECASE),
    re.compile(r"send\s+application", re.IGNORECASE),
    re.compile(r"apply\s+now", re.IGNORECASE),
    re.compile(r"^\s*apply\s*$", re.IGNORECASE),
    re.compile(r"finish\s+application", re.IGNORECASE),
    re.compile(r"complete\s+application", re.IGNORECASE),
)


class SubmitGuardError(RuntimeError):
    """Raised when an action looks like final application submission."""


def looks_like_submit_action(label: str | None) -> bool:
    text = (label or "").strip()
    if not text:
        return False
    return any(p.search(text) for p in SUBMIT_DENYLIST_PATTERNS)


def assert_not_submit_action(label: str | None, *, context: str = "click") -> None:
    if looks_like_submit_action(label):
        raise SubmitGuardError(
            f"Blocked {context}: refusing submit-like action {label!r}. "
            "Ganesha never clicks final Submit."
        )
