from __future__ import annotations

from urllib.parse import urlparse


def is_safe_http_url(url: str | None) -> bool:
    if not url or not str(url).strip():
        return False
    parsed = urlparse(str(url).strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    # Block local/metadata targets that should never be "application" destinations.
    host = parsed.hostname or ""
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return False
    if host.endswith(".local"):
        return False
    return True


def same_application_target(intended: str, candidate: str) -> bool:
    """Require exact match of the prepared application URL (no arbitrary redirects)."""
    return intended.strip() == candidate.strip()
