"""Cover-letter output paths. Resume-folder accents are resolved at runtime."""

from __future__ import annotations

import unicodedata
from pathlib import Path

from packages.core.settings import settings

TEMPLATE_VERSION = "ganesha-cover-v1"
OUTPUT_BASENAME = "Alex_Michael_Manjakunnel_Cover"
CANDIDATE_NAME = "Alex Michael Manjakunnel"
REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "data" / "cover_letter_cache"
PROMPT_VERSION = "cover-letter-v1"

VOICE_MODES = (
    "technical_direct",
    "product_oriented",
    "mission_oriented",
    "startup_fast_paced",
    "large_company_professional",
)
DEFAULT_VOICE = "technical_direct"

ELIGIBILITY_BLOCK_MSG = (
    "Cover-letter generation is unavailable because this job does not meet your "
    "configured eligibility policy."
)
INSUFFICIENT_MSG = (
    "Cannot generate a reliable cover letter because this job does not contain "
    "enough job-description detail."
)
MISSING_URL_MSG = (
    "Cover-letter generation blocked: this job is missing a valid application URL."
)
INCOMPLETE_IDENTITY_MSG = (
    "Cannot generate a cover letter because the company or role name is incomplete."
)
NO_EVIDENCE_MSG = (
    "Cannot generate a cover letter because no verified candidate evidence matches "
    "this role."
)
COMPANY_EVIDENCE_UNAVAILABLE = (
    "Company-specific evidence unavailable; generated from job description and "
    "verified candidate evidence."
)
MIN_DESCRIPTION_CHARS = 400


def desktop_pdf_path() -> Path:
    return Path(settings.cover_desktop_pdf_path)


def _accent_candidates(parent: Path) -> list[Path]:
    found: list[Path] = []
    if not parent.is_dir():
        return found
    for child in parent.iterdir():
        if not child.is_dir():
            continue
        folded = unicodedata.normalize("NFKD", child.name).encode("ascii", "ignore")
        if folded.decode().lower() in {"resumes", "resume"}:
            found.append(child)
    return found


def resolve_cover_letters_dir() -> Path:
    """Return the local Cover Letters folder, creating it when the parent exists."""
    configured = (settings.ganesha_cover_letters_dir or "").strip()
    if configured:
        path = Path(configured)
        path.mkdir(parents=True, exist_ok=True)
        return path

    source = Path(settings.resume_source_path)
    if source.exists():
        target = source / "Cover Letters"
        target.mkdir(parents=True, exist_ok=True)
        return target

    documents = Path.home() / "Documents"
    for resume_dir in _accent_candidates(documents):
        target = resume_dir / "Cover Letters"
        target.mkdir(parents=True, exist_ok=True)
        return target

    for form in ("NFC", "NFD"):
        name = unicodedata.normalize(form, "Resumés")
        parent = documents / name
        if parent.is_dir():
            target = parent / "Cover Letters"
            target.mkdir(parents=True, exist_ok=True)
            return target

    fallback = documents / "Resumes" / "Cover Letters"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def failed_root() -> Path:
    return resolve_cover_letters_dir() / "_failed"
