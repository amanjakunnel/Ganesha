"""Small referral-company careers pilot (not a broad crawler)."""

from __future__ import annotations

import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from packages.core.services.company_normalize import normalize_company_key
from packages.core.services.importers.referral_contacts_sheet import (
    fetch_referral_sheet_csv,
    unique_companies_from_sheet_csv,
)
from packages.core.services.job_intake import upsert_job_from_import

# Hand-verified official careers entry points for the pilot only.
PILOT_CAREERS_URLS: dict[str, str] = {
    "databricks": "https://www.databricks.com/company/careers",
    "microsoft": "https://careers.microsoft.com/",
    "amazon": "https://www.amazon.jobs/",
    "aws": "https://www.amazon.jobs/",
    "apple": "https://jobs.apple.com/",
    "snowflake": "https://careers.snowflake.com/",
    "stripe": "https://stripe.com/jobs",
    "nvidia": "https://www.nvidia.com/en-us/about-nvidia/careers/",
}

_JOB_HREF = re.compile(
    r'href="([^"]+/(?:jobs?|careers?|position)[^"]*)"', re.IGNORECASE
)


def run_referral_careers_pilot(
    session: Session,
    *,
    limit_companies: int = 5,
    dry_run: bool = True,
) -> dict[str, Any]:
    fetched_sheet = fetch_referral_sheet_csv()
    notes: list[str] = []
    if not fetched_sheet.ok or not fetched_sheet.csv_text:
        return {
            "dry_run": dry_run,
            "companies": [],
            "fetched": 0,
            "ingested": 0,
            "skipped": 0,
            "notes": [fetched_sheet.error or "sheet unavailable"],
        }

    companies = unique_companies_from_sheet_csv(fetched_sheet.csv_text)[:limit_companies]
    ingested = 0
    skipped = 0
    fetched = 0

    for company in companies:
        key = normalize_company_key(company)
        careers = PILOT_CAREERS_URLS.get(key)
        if not careers:
            notes.append(f"skip {company}: no verified official careers URL in pilot map")
            skipped += 1
            continue
        try:
            response = httpx.get(
                careers,
                follow_redirects=True,
                timeout=20.0,
                headers={"User-Agent": "GaneshaReferralPilot/0.1 (local; +https://localhost)"},
            )
        except Exception as exc:
            notes.append(f"skip {company}: fetch error {type(exc).__name__}")
            skipped += 1
            continue
        if response.status_code in {401, 403, 429}:
            notes.append(
                f"skip {company}: HTTP {response.status_code} (not bypassing protections)"
            )
            skipped += 1
            continue
        if response.status_code >= 400:
            notes.append(f"skip {company}: HTTP {response.status_code}")
            skipped += 1
            continue
        fetched += 1
        # Pilot records the verified careers entry itself as a discovery seed,
        # not every localized marketing careers mirror link.
        seed_url = careers
        title = f"{company} — careers listing (pilot seed)"
        payload = {
            "title": title,
            "company": company,
            "description_text": (
                f"Company: {company}\nRole: {title}\nLocation: United States\n"
                f"Source: referral_careers_pilot\nCareers: {careers}\n"
                "Pilot seed from a verified official careers entry point. "
                "Open the careers site to find concrete requisitions; eligibility "
                "and fit still apply."
            ),
            "company_apply_url": seed_url,
            "canonical_url": seed_url,
            "location": "United States",
            "source_import_key": f"referral_careers|{key}|{seed_url}",
        }
        href_count = len(_JOB_HREF.findall(response.text))
        notes.append(
            f"{company}: careers reachable ({response.status_code}); "
            f"parsed_href_candidates={href_count}"
        )
        if dry_run:
            notes.append(f"dry-run would ingest seed {company}: {seed_url}")
            ingested += 1
            continue
        job_id, created = upsert_job_from_import(
            session,
            payload,
            source_name="referral_careers_pilot",
            source_type="company_page",
        )
        notes.append(
            f"{'created' if created else 'deduped'} {company} job_id={job_id}"
        )
        ingested += 1

    return {
        "dry_run": dry_run,
        "companies": companies,
        "fetched": fetched,
        "ingested": ingested,
        "skipped": skipped,
        "notes": notes,
    }
