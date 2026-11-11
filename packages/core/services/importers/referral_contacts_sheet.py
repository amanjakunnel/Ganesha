"""Live Google Sheet adapter for referral contacts (viewable CSV export)."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
import httpx
from sqlalchemy.orm import Session

from packages.core.services.importers.referral_contacts_xlsx import (
    import_referral_contacts_xlsx,
)
from packages.core.services.job_intake import ImportResult
from packages.core.settings import settings

EXPECTED_HEADERS = {
    "Company",
    "Name",
    "Position",
    "Team",
    "Location(s)",
    "Alternate Location",
}


@dataclass
class SheetFetchResult:
    ok: bool
    status_code: int | None = None
    headers: list[str] = field(default_factory=list)
    row_count: int = 0
    error: str | None = None
    csv_text: str | None = None


def referral_sheet_export_url(
    sheet_id: str | None = None, gid: str | None = None
) -> str:
    sid = sheet_id or settings.referral_sheet_id
    g = gid if gid is not None else settings.referral_sheet_gid
    return (
        f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={g}"
    )


def fetch_referral_sheet_csv(
    *,
    sheet_id: str | None = None,
    gid: str | None = None,
    timeout: float = 30.0,
) -> SheetFetchResult:
    url = referral_sheet_export_url(sheet_id=sheet_id, gid=gid)
    try:
        response = httpx.get(url, follow_redirects=True, timeout=timeout)
    except Exception as exc:  # network / TLS
        return SheetFetchResult(ok=False, error=f"{type(exc).__name__}: {exc}")
    if response.status_code != 200:
        return SheetFetchResult(
            ok=False,
            status_code=response.status_code,
            error=f"HTTP {response.status_code} fetching sheet CSV export",
        )
    ctype = (response.headers.get("content-type") or "").lower()
    text = response.text
    if "text/html" in ctype and "sign in" in text.lower():
        return SheetFetchResult(
            ok=False,
            status_code=response.status_code,
            error="Sheet requires sign-in; enable link sharing or provide access",
        )
    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    rows = list(reader)
    return SheetFetchResult(
        ok=True,
        status_code=response.status_code,
        headers=headers,
        row_count=len(rows),
        csv_text=text,
    )


def import_referral_contacts_from_live_sheet(
    session: Session,
    *,
    cache_path: str,
    sheet_id: str | None = None,
    gid: str | None = None,
) -> tuple[ImportResult, SheetFetchResult]:
    """Fetch the live sheet, cache CSV locally (gitignored), import via CSV→xlsx path.

    Does not fall back to a stale xlsx when fetch fails.
    """
    fetched = fetch_referral_sheet_csv(sheet_id=sheet_id, gid=gid)
    if not fetched.ok or not fetched.csv_text:
        res = ImportResult()
        res.errors.append(fetched.error or "sheet fetch failed")
        return res, fetched

    missing = EXPECTED_HEADERS.difference(set(fetched.headers))
    if missing:
        res = ImportResult()
        res.errors.append(f"unexpected sheet headers; missing {sorted(missing)}")
        return res, fetched

    # Convert CSV to a temporary xlsx-compatible import by writing CSV and using
    # openpyxl-less path: reuse xlsx importer after writing a minimal workbook.
    from openpyxl import Workbook
    from pathlib import Path

    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    reader = csv.reader(io.StringIO(fetched.csv_text))
    for row in reader:
        ws.append(row)
    # Store as xlsx cache under referral_cache (gitignored).
    xlsx_path = path.with_suffix(".xlsx") if path.suffix.lower() != ".xlsx" else path
    wb.save(xlsx_path)
    # Also keep raw CSV beside it for audit (gitignored directory).
    path.with_suffix(".csv").write_text(fetched.csv_text, encoding="utf-8")
    result = import_referral_contacts_xlsx(session, str(xlsx_path))
    return result, fetched


def unique_companies_from_sheet_csv(csv_text: str) -> list[str]:
    reader = csv.DictReader(io.StringIO(csv_text))
    companies: list[str] = []
    seen: set[str] = set()
    for row in reader:
        name = (row.get("Company") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        companies.append(name)
    return companies
