from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any
from urllib.parse import urlparse

from packages.core.domain.models import hash_text

SIMPLIFYJOBS_REPO_URL = "https://github.com/SimplifyJobs/New-Grad-Positions"
SIMPLIFYJOBS_README_RAW = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md"
)

EMOJI_NO_SPONSORSHIP = "🛂"
EMOJI_US_CITIZENSHIP = "🇺🇸"
EMOJI_CLOSED = "🔒"
EMOJI_ADVANCED_DEGREE = "🎓"


@dataclass
class ParsedSimplifyJob:
    company: str
    title: str
    location: str | None
    apply_url: str | None
    age_days: int | None
    category: str | None
    source_row_url: str
    markers: dict[str, bool] = field(default_factory=dict)
    raw_cells: dict[str, str] = field(default_factory=dict)


def _parse_table_rows(section_html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", section_html, flags=re.DOTALL | re.IGNORECASE):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.DOTALL | re.IGNORECASE)
        if cells:
            rows.append([cell.strip() for cell in cells])
    return rows


def _strip_tags(text: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(no_tags)).strip()


def _extract_company_name(cell: str) -> str | None:
    anchor = re.search(r"<a[^>]*>([^<]+)</a>", cell, re.IGNORECASE)
    if anchor:
        text = unescape(anchor.group(1)).strip()
    else:
        text = _strip_tags(cell)
    if not text or text == "↳":
        return None
    return text.replace("🔥", "").strip() or None


def _extract_apply_url(cell: str) -> str | None:
    if EMOJI_CLOSED in cell and "href=" not in cell:
        return None
    hrefs = re.findall(r'href="([^"]+)"', cell)
    for href in hrefs:
        host = urlparse(href).netloc.lower()
        if "simplify.jobs" in host:
            continue
        if href.startswith("http"):
            return str(href)
    return None


def _parse_age_days(age_cell: str) -> int | None:
    text = _strip_tags(age_cell)
    m = re.match(r"^(\d+)d$", text)
    if m:
        return int(m.group(1))
    return None


def _age_to_posted_at(age_days: int | None, *, synced_at: datetime) -> datetime | None:
    if age_days is None:
        return None
    return synced_at - timedelta(days=age_days)


def _detect_markers(company_cell: str, role_cell: str, application_cell: str) -> dict[str, bool]:
    role_blob = f"{company_cell} {role_cell}"
    app = application_cell.strip()
    return {
        "no_sponsorship": EMOJI_NO_SPONSORSHIP in role_blob,
        "citizenship_required": EMOJI_US_CITIZENSHIP in role_blob,
        "listing_closed": EMOJI_CLOSED in app and "href=" not in app,
    }


def _clean_title(title: str) -> str:
    for emoji in (
        EMOJI_NO_SPONSORSHIP,
        EMOJI_US_CITIZENSHIP,
        EMOJI_CLOSED,
        EMOJI_ADVANCED_DEGREE,
        "🔥",
    ):
        title = title.replace(emoji, "")
    return re.sub(r"\s+", " ", title).strip()


def parse_simplifyjobs_readme(
    readme_html: str,
    *,
    synced_at: datetime | None = None,
) -> list[ParsedSimplifyJob]:
    """Parse SimplifyJobs New-Grad README table rows."""
    synced = synced_at or datetime.now(UTC)
    if synced.tzinfo is None:
        synced = synced.replace(tzinfo=UTC)

    current_company: str | None = None
    parsed: list[ParsedSimplifyJob] = []

    sections = re.split(r"(?=##\s+)", readme_html)
    for section in sections:
        heading_match = re.match(r"##\s+(.+?)\n", section)
        category = heading_match.group(1).strip() if heading_match else None
        section_company = current_company
        for cells in _parse_table_rows(section):
            if len(cells) < 4:
                continue
            if cells[0].lower().startswith("company"):
                continue
            company_cell, role_cell, location_cell = cells[0], cells[1], cells[2]
            application_cell = cells[3]
            age_cell = cells[4] if len(cells) > 4 else ""

            company_name = _extract_company_name(company_cell)
            if company_name:
                section_company = company_name
            if not section_company:
                continue

            title_raw = _strip_tags(role_cell)
            if not title_raw or title_raw == EMOJI_CLOSED:
                continue

            markers = _detect_markers(company_cell, role_cell, application_cell)
            apply_url = _extract_apply_url(application_cell)
            age_days = _parse_age_days(age_cell)
            title = _clean_title(title_raw)
            location = _strip_tags(location_cell) or None
            source_row_url = apply_url or f"{SIMPLIFYJOBS_REPO_URL}#row"

            parsed.append(
                ParsedSimplifyJob(
                    company=section_company,
                    title=title,
                    location=location,
                    apply_url=apply_url,
                    age_days=age_days,
                    category=category,
                    source_row_url=source_row_url,
                    markers=markers,
                    raw_cells={
                        "company": company_cell,
                        "role": role_cell,
                        "location": location_cell,
                        "application": application_cell,
                        "age": age_cell,
                    },
                )
            )
        if section_company:
            current_company = section_company

    return parsed


def simplifyjobs_import_key(
    apply_url: str | None, company: str, title: str, location: str | None
) -> str:
    if apply_url:
        return hash_text(f"simplifyjobs|{apply_url}")
    return hash_text(f"simplifyjobs|{company}|{title}|{location or ''}")


def parsed_row_to_import_dict(
    row: ParsedSimplifyJob, *, synced_at: datetime
) -> dict[str, Any]:
    posted_at = _age_to_posted_at(row.age_days, synced_at=synced_at)
    description_parts = [
        f"Company: {row.company}",
        f"Role: {row.title}",
    ]
    if row.location:
        description_parts.append(f"Location: {row.location}")
    if row.category:
        description_parts.append(f"Category: {row.category}")
    description_parts.append(f"Source: {SIMPLIFYJOBS_REPO_URL}")
    description = "\n".join(description_parts)

    return {
        "title": row.title,
        "company": row.company,
        "description_text": description,
        "location": row.location,
        "company_apply_url": row.apply_url,
        "canonical_url": row.source_row_url,
        "posted_at": posted_at.isoformat() if posted_at else None,
        "scraped_at": synced_at.isoformat(),
        "employment_type": "full_time",
        "source_import_key": simplifyjobs_import_key(
            row.apply_url, row.company, row.title, row.location
        ),
        "intake_metadata": {
            "simplify_markers": row.markers,
            "simplify_category": row.category,
            "simplify_age_days": row.age_days,
            "source_repo": SIMPLIFYJOBS_REPO_URL,
        },
        "raw_payload": row.raw_cells,
    }
