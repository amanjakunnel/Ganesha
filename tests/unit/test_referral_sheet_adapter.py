from __future__ import annotations

from packages.core.services.importers.referral_contacts_sheet import (
    EXPECTED_HEADERS,
    unique_companies_from_sheet_csv,
)


def test_unique_companies_from_synthetic_sheet() -> None:
    csv_text = (
        "Company,Name,Position,Team,Location(s),Alternate Location\n"
        "Databricks,Ada,Engineer,Lakebase,\"Sunnyvale, CA\",\n"
        ",Bob,Engineer,Other,\"Seattle, WA\",\n"
        "Databricks,Cara,Engineer,Delta,\"Sunnyvale, CA\",\n"
        "Microsoft,Dan,Engineer,,\"Redmond, WA\",\n"
    )
    companies = unique_companies_from_sheet_csv(csv_text)
    assert companies == ["Databricks", "Microsoft"]
    assert EXPECTED_HEADERS.issubset(
        {
            "Company",
            "Name",
            "Position",
            "Team",
            "Location(s)",
            "Alternate Location",
        }
    )


def test_gitignore_covers_private_import_paths() -> None:
    from pathlib import Path

    text = Path(".gitignore").read_text(encoding="utf-8")
    for needle in (
        "data/browser_profiles",
        "data/import_archive",
        "data/referral_cache",
        "Simplify_Tracked_Jobs",
        "*.sqlite",
        ".env",
    ):
        assert needle in text
