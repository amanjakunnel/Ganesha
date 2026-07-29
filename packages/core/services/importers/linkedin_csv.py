from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from packages.core.domain.models import hash_text
from packages.core.services.company_normalize import canonical_company_display
from packages.core.services.job_intake import ImportResult, upsert_job_from_import


def _pick_description(row: dict[str, str]) -> str:
    for key in ("Primary Description", "Description", "Description HTML"):
        val = (row.get(key) or "").strip()
        if len(val) >= 40:
            return val
    return (row.get("Description") or row.get("Primary Description") or "").strip()


def _linkedin_import_key(detail_url: str | None, apply_url: str | None, title: str, company: str) -> str:
    base = detail_url or apply_url or f"{company}|{title}"
    return hash_text(f"linkedin|{base}")


def parse_linkedin_row(row: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    title = (row.get("Title") or "").strip()
    company = (row.get("Company Name") or "").strip()
    description = _pick_description(row)
    if not title or not company:
        return None, "missing title or company"
    if not description:
        return None, "missing description"
    detail_url = (row.get("Detail URL") or "").strip() or None
    apply_url = (row.get("Company Apply Url") or "").strip() or None
    location = (row.get("Location") or "").strip() or None
    return {
        "title": title,
        "company": canonical_company_display(company),
        "description_text": description,
        "canonical_url": detail_url,
        "company_apply_url": apply_url,
        "location": location,
        "posted_at": row.get("Created At"),
        "scraped_at": row.get("Scraped At"),
        "external_id": (row.get("Poster Id") or "").strip() or None,
        "raw_payload": {k: v for k, v in row.items() if v},
        "source_import_key": _linkedin_import_key(detail_url, apply_url, title, company),
    }, None


def import_linkedin_csv(session: Session, path: str) -> ImportResult:
    res = ImportResult()
    p = Path(path)
    if not p.is_file():
        res.errors.append(f"file not found: {path}")
        return res
    with p.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader, start=1):
            data, err = parse_linkedin_row(row)
            if err:
                res.errors.append(f"row {i}: {err}")
                continue
            assert data is not None
            _, created = upsert_job_from_import(
                session,
                data,
                source_name="linkedin",
                source_type="csv_import",
                legacy_source="csv_import",
            )
            if created:
                res.created += 1
            else:
                res.duplicates += 1
    return res
