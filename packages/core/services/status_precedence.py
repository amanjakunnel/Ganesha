"""Application-history status precedence for Simplify imports.

Most advanced status wins when the same posting appears with multiple rows.
APPLIED (and anything more advanced) is never downgraded by a later SAVED row.

Precedence (low → high):
  unknown / blank < saved_only < applied < screen < interview < offer < accepted

Terminal negative outcomes (rejected, withdrawn, ghosted) sit above applied so
a rejection is not collapsed back to saved_only, but they do not outrank offer/
accepted. Documented edge case: competing terminal states keep the higher rank.
"""

from __future__ import annotations

STATUS_PRECEDENCE: dict[str, int] = {
    "": 0,
    "unknown": 0,
    "saved_only": 10,
    "applied": 40,
    "screen": 50,
    "interview": 60,
    "offer": 70,
    "accepted": 80,
    "ghosted": 45,
    "rejected": 45,
    "withdrawn": 45,
}

# Explicit list for docs/tests.
PRECEDENCE_ORDER_LOW_TO_HIGH: tuple[str, ...] = (
    "unknown",
    "saved_only",
    "applied",
    "ghosted",
    "rejected",
    "withdrawn",
    "screen",
    "interview",
    "offer",
    "accepted",
)


def status_rank(status: str | None) -> int:
    if not status:
        return 0
    key = status.strip().lower()
    return STATUS_PRECEDENCE.get(key, 0)


def is_at_least(status: str | None, minimum: str) -> bool:
    return status_rank(status) >= status_rank(minimum)


def prefer_status(current: str | None, incoming: str | None) -> str | None:
    """Return the more advanced of two mapped statuses (never downgrade)."""
    if status_rank(incoming) > status_rank(current):
        return incoming
    return current


def would_downgrade(current: str | None, incoming: str | None) -> bool:
    """True if applying incoming would lower precedence (e.g. APPLIED → SAVED)."""
    if current is None or incoming is None:
        return False
    return status_rank(incoming) < status_rank(current)
