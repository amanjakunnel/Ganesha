"""Job intake and triage commands (non-production local use only)."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import typer
from sqlalchemy.orm import Session

from packages.core.db import SessionLocal
from packages.core.services.job_service import (
    import_csv,
    import_json,
    assess_job,
    list_review_queue,
    start_referral,
)
from packages.core.domain.models import JobPosting, ReferralTask, AuditEvent

jobs_app = typer.Typer(help="Job intake and triage commands")

SHEETS_DIR = Path("sheets")
DEFAULT_LINKEDIN_CSV = SHEETS_DIR / "JobsScraperForLinkedIn_13_2026-07-29.csv"
DEFAULT_SYMPLICITY_XLSX = SHEETS_DIR / "Symplicity Example.xlsx"
DEFAULT_REFERRAL_XLSX = SHEETS_DIR / "Referral Contacts.xlsx"


def _print_import_result(label: str, res: object) -> None:
    from packages.core.services.job_intake import ImportResult

    if not isinstance(res, ImportResult):
        return
    typer.echo(
        f"{label}: created={res.created}, updated={getattr(res, 'updated', 0)}, "
        f"duplicates={res.duplicates}, errors={len(res.errors)}"
    )
    for e in res.errors[:20]:
        typer.echo(f"  error: {e}")
    if len(res.errors) > 20:
        typer.echo(f"  ... and {len(res.errors) - 20} more errors")


@jobs_app.command(name="import-linkedin")
def import_linkedin_cmd(
    path: str = typer.Option(
        str(DEFAULT_LINKEDIN_CSV), "--path", help="LinkedIn scraper CSV"
    ),
) -> None:
    """Import jobs from the LinkedIn CSV export in sheets/."""
    from packages.core.services.importers.linkedin_csv import import_linkedin_csv

    session: Session = SessionLocal()
    try:
        res = import_linkedin_csv(session, path)
        session.commit()
        _print_import_result("LinkedIn import", res)
    finally:
        session.close()


@jobs_app.command(name="import-symplicity")
def import_symplicity_cmd(
    path: str = typer.Option(
        str(DEFAULT_SYMPLICITY_XLSX), "--path", help="Symplicity XLSX"
    ),
) -> None:
    """Import jobs from a manual Symplicity XLSX export."""
    from packages.core.services.importers.symplicity_xlsx import import_symplicity_xlsx

    session: Session = SessionLocal()
    try:
        res = import_symplicity_xlsx(session, path)
        session.commit()
        _print_import_result("Symplicity import", res)
    finally:
        session.close()


@jobs_app.command(name="import-referrals")
def import_referrals_cmd(
    from_sheet: bool = typer.Option(
        True, "--from-sheet/--from-xlsx", help="Use live Google Sheet (default)"
    ),
    path: str = typer.Option(
        str(DEFAULT_REFERRAL_XLSX),
        "--path",
        help="Fallback local XLSX only when --from-xlsx is set",
    ),
) -> None:
    """Import referral contact enrichment data (not jobs). Live sheet is source of truth."""
    from pathlib import Path

    from packages.core.services.importers.referral_contacts_sheet import (
        import_referral_contacts_from_live_sheet,
    )
    from packages.core.services.importers.referral_contacts_xlsx import (
        import_referral_contacts_xlsx,
    )

    session: Session = SessionLocal()
    try:
        if from_sheet:
            cache = Path("data/referral_cache/referral_contacts_live.xlsx")
            res, meta = import_referral_contacts_from_live_sheet(
                session, cache_path=str(cache)
            )
            if not meta.ok:
                typer.echo(f"Live sheet fetch failed: {meta.error}")
                typer.echo("Refusing to fall back to a stale local xlsx.")
                raise typer.Exit(code=1)
            typer.echo(
                f"Live sheet ok status={meta.status_code} headers={meta.headers} "
                f"rows={meta.row_count}"
            )
            session.commit()
            _print_import_result("Referral contacts (live sheet)", res)
        else:
            res = import_referral_contacts_xlsx(session, path)
            session.commit()
            _print_import_result("Referral contacts import (local xlsx)", res)
    finally:
        session.close()


@jobs_app.command(name="import-simplify-tracker")
def import_simplify_tracker_cmd(
    path: str = typer.Argument(..., help="Path to Simplify Job Tracker export CSV"),
    dry_run: bool = typer.Option(
        True, "--dry-run/--apply", help="Preview by default; pass --apply to write"
    ),
) -> None:
    """One-way import of a Simplify Job Tracker CSV export (not live sync)."""
    from packages.core.services.importers.simplify_tracker_csv import (
        import_simplify_tracker_csv,
    )

    session: Session = SessionLocal()
    try:
        report = import_simplify_tracker_csv(session, path, dry_run=dry_run)
        if not dry_run:
            session.commit()
        data = report.to_dict()
        typer.echo(
            "Simplify tracker CSV import: "
            f"dry_run={data['dry_run']} rows={data['rows_read']} "
            f"collapsed_dups={data['duplicates_collapsed']} "
            f"matched_jobs={data['matched_existing_jobs']} "
            f"new={data['new_records']} already={data['already_imported']} "
            f"ambiguous={data['ambiguous_count']} "
            f"applied_like={data['applied_like']} saved_only={data['saved_only']}"
        )
        typer.echo(f"Headers: {data['headers']}")
        typer.echo(f"Status mapping: {data['status_mapping']}")
        typer.echo(f"Status counts: {data['status_counts']}")
        if data["archive_path"]:
            typer.echo(f"Archive copy: {data['archive_path']}")
        for err in data.get("errors") or []:
            typer.echo(f"  error: {err}")
        for amb in (data.get("ambiguous") or [])[:10]:
            typer.echo(f"  ambiguous: {amb}")
    finally:
        session.close()


@jobs_app.command(name="referral-careers-pilot")
def referral_careers_pilot_cmd(
    limit_companies: int = typer.Option(5, "--limit-companies"),
    dry_run: bool = typer.Option(True, "--dry-run/--apply"),
) -> None:
    """Small pilot: unique referral companies → official careers URLs → ingest jobs."""
    from packages.core.services.referral_careers_pilot import run_referral_careers_pilot

    session: Session = SessionLocal()
    try:
        result = run_referral_careers_pilot(
            session, limit_companies=limit_companies, dry_run=dry_run
        )
        if not dry_run:
            session.commit()
        typer.echo(
            "Referral careers pilot: "
            f"dry_run={result['dry_run']} companies={result['companies']} "
            f"fetched={result['fetched']} ingested={result['ingested']} "
            f"skipped={result['skipped']}"
        )
        for note in result.get("notes") or []:
            typer.echo(f"  {note}")
    finally:
        session.close()


@jobs_app.command(name="import-sheets")
def import_sheets_cmd() -> None:
    """Import LinkedIn, Symplicity, and referral contacts from Ganesha/sheets."""
    from packages.core.services.importers.linkedin_csv import import_linkedin_csv
    from packages.core.services.importers.referral_contacts_xlsx import (
        import_referral_contacts_xlsx,
    )
    from packages.core.services.importers.symplicity_xlsx import import_symplicity_xlsx

    session: Session = SessionLocal()
    try:
        li = import_linkedin_csv(session, str(DEFAULT_LINKEDIN_CSV))
        sym = import_symplicity_xlsx(session, str(DEFAULT_SYMPLICITY_XLSX))
        ref = import_referral_contacts_xlsx(session, str(DEFAULT_REFERRAL_XLSX))
        session.commit()
        _print_import_result("LinkedIn", li)
        _print_import_result("Symplicity", sym)
        _print_import_result("Referrals", ref)
    finally:
        session.close()


@jobs_app.command(name="import-csv")
def import_csv_cmd(path: str) -> None:
    """Import jobs from a CSV file."""
    session: Session = SessionLocal()
    try:
        res = import_csv(session, path)
        session.commit()
        typer.echo(
            f"Imported: {res.created}, Duplicates: {res.duplicates}, Errors: {len(res.errors)}"
        )
        for e in res.errors or []:
            typer.echo(f"Error: {e}")
    finally:
        session.close()


@jobs_app.command(name="import-json")
def import_json_cmd(path: str) -> None:
    """Import jobs from a JSON file."""
    session: Session = SessionLocal()
    try:
        res = import_json(session, path)
        session.commit()
        typer.echo(
            f"Imported: {res.created}, Duplicates: {res.duplicates}, Errors: {len(res.errors)}"
        )
    finally:
        session.close()


@jobs_app.command()
def add(title: str, company: str, description_file: str | None = None) -> None:
    """Manually add a job posting."""
    session: Session = SessionLocal()
    try:
        if description_file:
            with open(description_file, encoding="utf-8") as fh:
                description = fh.read()
        else:
            typer.echo("Enter description (end with EOF / Ctrl-D):")
            description = sys.stdin.read()
        data = {"title": title, "company": company, "description_text": description}
        from packages.core.services.job_service import _create_or_flag

        created = _create_or_flag(session, data, source="manual")
        session.commit()
        typer.echo(f"Created: {created}")
    finally:
        session.close()


@jobs_app.command()
def list(
    source: str | None = typer.Option(None, "--source"),
    limit: int = 100,
) -> None:
    """List recent job postings."""
    session: Session = SessionLocal()
    try:
        q = session.query(JobPosting).order_by(JobPosting.created_at.desc())
        if source:
            q = q.filter(JobPosting.source_name == source)
        rows = q.limit(limit).all()
        for r in rows:
            company_name = r.company.canonical_name if r.company else "Unknown"
            typer.echo(
                f"{r.id} | {r.source_name or r.source} | {r.title} | {r.status} | {company_name}"
            )
    finally:
        session.close()


@jobs_app.command()
def show(
    job_id: str, ranked: bool = typer.Option(True, "--ranked/--no-ranked")
) -> None:
    """Show details of a specific job posting."""
    session: Session = SessionLocal()
    try:
        if ranked:
            from packages.core.services.job_display import format_job_detail_cli
            from packages.core.services.job_search_service import (
                job_detail_with_ranking,
            )
            from packages.core.services.workflow_service import compute_job_lifecycle

            try:
                detail = job_detail_with_ranking(session, job_id)
            except LookupError:
                typer.echo("Not found")
                raise typer.Exit(code=1)
            lifecycle = compute_job_lifecycle(session, job_id)
            typer.echo(format_job_detail_cli(detail, lifecycle=lifecycle))
            return

        j = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
        if j is None:
            typer.echo("Not found")
            raise typer.Exit(code=1)
        typer.echo(f"ID: {j.id}")
        typer.echo(f"Title: {j.title}")
        company_name = j.company.canonical_name if j.company else "Unknown"
        typer.echo(f"Company: {company_name}")
        typer.echo(f"Location: {j.location}")
        typer.echo(f"Status: {j.status}")
        desc_text = j.description_text or ""
        excerpt = desc_text[:500] + ("..." if len(desc_text) > 500 else "")
        typer.echo(f"Description:\n{excerpt}")
    finally:
        session.close()


@jobs_app.command()
def assess(job_id: str) -> None:
    """Assess a job posting and recommend a resume track."""
    session: Session = SessionLocal()
    try:
        a = assess_job(session, job_id)
        session.commit()
        typer.echo(f"Recommended track: {a.recommended_track}, score: {a.score}")
        typer.echo(f"Explanation: {a.score_explanation}")
    finally:
        session.close()


@jobs_app.command(name="sync-simplifyjobs")
def sync_simplifyjobs_cmd(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Parse without writing to the database"
    ),
    limit: int | None = typer.Option(
        None, "--limit", help="Limit parsed rows (testing)"
    ),
) -> None:
    """Sync new-grad roles from the public SimplifyJobs GitHub repository."""
    from packages.core.services.simplifyjobs_sync import (
        SimplifyJobsFetchError,
        sync_simplifyjobs,
    )

    session: Session = SessionLocal()
    try:
        try:
            result = sync_simplifyjobs(session, dry_run=dry_run, limit=limit)
        except SimplifyJobsFetchError as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(code=2)
        if not dry_run:
            session.commit()
        typer.echo(
            "SimplifyJobs sync: "
            f"fetched={'yes' if result.fetched else 'no'}, "
            f"parsed={result.parsed}, created={result.created}, "
            f"updated={result.updated}, duplicates={result.duplicates}, "
            f"closed={result.closed}, "
            f"actionable={result.actionable}, needs_review={result.needs_review}, "
            f"excluded={result.excluded}, errors={len(result.errors)}"
        )
        for err in result.errors[:10]:
            typer.echo(f"  error: {err}")
    finally:
        session.close()


@jobs_app.command(name="resolve-company-boards")
def resolve_company_boards_cmd(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Preview by default; --apply persists resolutions under ~/.ganesha/",
    ),
    limit: int = typer.Option(40, "--limit", help="Max referral companies / boards"),
    sync_new: bool = typer.Option(
        False,
        "--sync-new/--no-sync-new",
        help="After resolve, sync jobs only for newly resolved boards",
    ),
) -> None:
    """Resolve referral companies to ATS boards (job URLs + careers probes)."""
    from packages.core.services.board_discovery.company_resolution import (
        resolve_company_boards,
    )
    from packages.core.services.board_discovery import sync_company_boards

    session: Session = SessionLocal()
    try:
        result = resolve_company_boards(
            session,
            limit=limit,
            use_careers_resolver=True,
            persist_resolutions=not dry_run,
        )
        typer.echo(
            "Resolve company boards: "
            f"dry_run={dry_run} companies={len(result.companies)} "
            f"boards={len(result.boards)} careers_resolved={result.careers_resolved} "
            f"unresolved={len(result.unresolved)}"
        )
        if result.sheet_error:
            typer.echo(f"  sheet_error: {result.sheet_error}")
        typer.echo("  vendor_counts:")
        for vendor, count in sorted((result.vendor_counts or {}).items()):
            typer.echo(f"    {vendor}: {count}")
        typer.echo("  unresolved_reasons:")
        for reason, count in sorted((result.unresolved_reason_counts or {}).items()):
            typer.echo(f"    {reason}: {count}")
        for ref in result.boards[:20]:
            typer.echo(
                f"  board: {ref.platform}/{ref.board_token} "
                f"company={ref.company}"
            )
        for u in result.unresolved[:20]:
            typer.echo(f"  unresolved: {u.company} ({u.reason})")
        if not dry_run and sync_new and result.newly_resolved_boards:
            sync_result = sync_company_boards(
                session,
                dry_run=False,
                limit=limit,
                boards=result.newly_resolved_boards,
            )
            session.commit()
            typer.echo(
                "Synced newly resolved: "
                f"boards_ok={sync_result.boards_ok}/{sync_result.boards_attempted} "
                f"new={sync_result.new} updated={sync_result.updated}"
            )
        elif not dry_run:
            session.commit()
    finally:
        session.close()


@jobs_app.command(name="sync-company-boards")
def sync_company_boards_cmd(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Preview by default; pass --apply to write",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Max referral companies to check (default: settings / 25)",
    ),
) -> None:
    """Discover jobs from Greenhouse / Lever / Ashby boards for referral companies."""
    from packages.core.services.board_discovery import sync_company_boards
    from packages.core.settings import settings

    effective_limit = (
        limit
        if limit is not None
        else int(settings.company_board_discovery_max_companies)
    )
    session: Session = SessionLocal()
    try:
        result = sync_company_boards(
            session,
            dry_run=dry_run,
            limit=effective_limit,
            persist_resolutions=not dry_run,
        )
        if not dry_run:
            session.commit()
        typer.echo(
            "Company board sync: "
            f"dry_run={result.dry_run} companies_checked={result.companies_checked} "
            f"boards_ok={result.boards_ok}/{result.boards_attempted} "
            f"fetched={result.fetched} new={result.new} updated={result.updated} "
            f"closed={result.closed} unresolved={len(result.unresolved)} "
            f"errors={len(result.errors)}"
        )
        for note in (result.notes or [])[:10]:
            typer.echo(f"  note: {note}")
        for u in result.unresolved[:15]:
            # Company name + reason only — never contact PII.
            typer.echo(f"  unresolved: {u.company} ({u.reason})")
        for err in result.errors[:10]:
            typer.echo(f"  error: {err}")
    finally:
        session.close()


@jobs_app.command(name="gmail-diagnose")
def gmail_diagnose_cmd(
    days: int | None = typer.Option(
        None,
        "--days",
        help="Override search window; default covers saved_only span",
    ),
) -> None:
    """Aggregate-only Gmail confirmation diagnosis (domains + counts; no persist)."""
    from packages.core.services.gmail_application_match import diagnose_gmail_preview

    session: Session = SessionLocal()
    try:
        result = diagnose_gmail_preview(session, days=days)
        data = result.to_dict()
        if data.get("setup_required"):
            typer.echo(data.get("setup_message") or "Gmail setup required")
            raise typer.Exit(code=2)
        typer.echo(
            "Gmail diagnose: "
            f"scanned={data['scanned']} matched={data['matched']} "
            f"low_confidence={data['low_confidence']} "
            f"unmatched={data['unmatched_aggregate']} "
            f"confirmation_looking={data['confirmation_looking']} "
            f"saved_only={data['saved_only_count']}"
        )
        search = data.get("search") or {}
        typer.echo(
            f"  search days={search.get('days')} max_results={search.get('max_results')} "
            f"page_token_paging={search.get('page_token_paging')}"
        )
        typer.echo(f"  query={search.get('query')}")
        span = data.get("saved_only_date_span") or {}
        typer.echo(f"  saved_only_span min={span.get('min')} max={span.get('max')}")
        typer.echo("  sender_domain_histogram:")
        for domain, count in (data.get("sender_domain_histogram") or {}).items():
            typer.echo(f"    {domain}: {count}")
        for line in data.get("explanation") or []:
            typer.echo(f"  note: {line}")
    finally:
        session.close()


@jobs_app.command(name="gmail-preview")
def gmail_preview_cmd(
    days: int | None = typer.Option(
        None,
        "--days",
        help="Search window days (default: cover saved_only span)",
    ),
    persist: bool = typer.Option(
        False,
        "--persist/--no-persist",
        help="Persist only when explicitly set AND matches>0 (default: false)",
    ),
) -> None:
    """Preview Gmail confirmation matches (aggregates; default no persist)."""
    from packages.core.services.gmail_application_match import preview_gmail_matches

    session: Session = SessionLocal()
    try:
        # Always preview first; only persist when asked and matches exist.
        preview = preview_gmail_matches(session, days=days, persist=False)
        data = preview.to_dict()
        if data.get("setup_required"):
            typer.echo(data.get("setup_message") or "Gmail setup required")
            raise typer.Exit(code=2)
        total_hits = int(data["matched"]) + int(data["low_confidence"])
        typer.echo(
            "Gmail preview: "
            f"scanned={data['scanned']} matched={data['matched']} "
            f"low_confidence={data['low_confidence']} "
            f"unmatched={data['unmatched_aggregate']} persist=False"
        )
        search = data.get("search") or {}
        typer.echo(
            f"  search days={search.get('days')} query={search.get('query')}"
        )
        for match in (data.get("matches") or [])[:20]:
            typer.echo(
                f"  hit: domain={match.get('sender_domain')} "
                f"company={match.get('matched_company')} "
                f"conf={match.get('confidence')}"
            )
        if persist and total_hits > 0:
            applied = preview_gmail_matches(session, days=days, persist=True)
            session.commit()
            typer.echo(
                "Gmail persist: "
                f"matched={applied.matched} low_confidence={applied.low_confidence}"
            )
        elif persist:
            typer.echo("Gmail persist skipped: matches=0")
@jobs_app.command(name="backfill-board-dates")
def backfill_board_dates_cmd(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Preview by default; pass --apply to write posted_at",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Max referral companies / boards to re-fetch (default: settings / 25)",
    ),
) -> None:
    """Re-fetch board APIs and backfill JobPosting.posted_at from vendor dates only."""
    from packages.core.services.board_discovery import backfill_board_dates
    from packages.core.settings import settings

    effective_limit = (
        limit
        if limit is not None
        else int(settings.company_board_discovery_max_companies)
    )
    session: Session = SessionLocal()
    try:
        result = backfill_board_dates(
            session, dry_run=dry_run, limit=effective_limit
        )
        if not dry_run:
            session.commit()
        typer.echo(
            "Board dates backfill: "
            f"dry_run={result.dry_run} companies_checked={result.companies_checked} "
            f"boards_ok={result.boards_ok}/{result.boards_attempted} "
            f"jobs_seen={result.jobs_seen} posted_at_set={result.posted_at_set} "
            f"already_had={result.already_had_posted_at} "
            f"no_source_date={result.no_source_date} "
            f"missing_local={result.missing_local} errors={len(result.errors)}"
        )
        for note in (result.notes or [])[:10]:
            typer.echo(f"  note: {note}")
        for err in result.errors[:10]:
            typer.echo(f"  error: {err}")
    finally:
        session.close()


@jobs_app.command(name="reassess-fit")
def reassess_fit_cmd(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview band counts without writing"
    ),
    limit: int | None = typer.Option(
        None, "--limit", help="Limit jobs reassessed (testing)"
    ),
) -> None:
    """Recompute evidence-fit bands for stored jobs without re-ingesting."""
    from packages.core.services.evidence_fit_service import reassess_jobs

    session: Session = SessionLocal()
    try:
        result = reassess_jobs(session, dry_run=dry_run, limit=limit)
        if not dry_run:
            session.commit()
        typer.echo(
            "Evidence fit reassessment: "
            f"scanned={result['scanned']}, dry_run={result['dry_run']}, "
            f"policy={result['policy_version']}, "
            f"candidate_facts={result['candidate_facts']}"
        )
        counts = result.get("counts") or {}
        for band, count in counts.items():
            typer.echo(f"  {band}: {count}")
    finally:
        session.close()


@jobs_app.command(name="classify-role-families")
def classify_role_families_cmd(
    apply: bool = typer.Option(
        False, "--apply", help="Write intake_metadata.role_profile (default dry-run)"
    ),
    limit: int = typer.Option(100, "--limit", help="Max jobs to classify"),
    refresh_profile: bool = typer.Option(
        False, "--refresh-profile", help="Re-derive role profile from evidence"
    ),
) -> None:
    """Layer-1: classify jobs against the derived role profile (cheap pre-filter)."""
    from packages.core.services.role_profile import (
        classify_jobs_batch,
        load_role_profile,
    )

    session: Session = SessionLocal()
    try:
        profile = load_role_profile(session, refresh=refresh_profile)
        typer.echo(
            "Role profile: "
            f"families={[f.id for f in profile.enabled_families()]} "
            f"hash={profile.evidence_hash}"
        )
        result = classify_jobs_batch(
            session, limit=limit, apply=apply, only_missing=True
        )
        if apply:
            session.commit()
        typer.echo(
            "Role-family classify: "
            f"scanned={result['scanned']} applied={result['applied']} "
            f"dry_run={result['dry_run']}"
        )
        for status, count in (result.get("counts") or {}).items():
            typer.echo(f"  {status}: {count}")
            for title in (result.get("examples") or {}).get(status, []):
                typer.echo(f"    · {title}")
    finally:
        session.close()


@jobs_app.command(name="assess-fit-batch")
def assess_fit_batch_cmd(
    limit: int = typer.Option(50, "--limit", help="Max unscored jobs to assess"),
    role_filtered_only: bool = typer.Option(
        True,
        "--role-filtered-only/--include-mismatches",
        help="Skip role_family_mismatch jobs (default)",
    ),
) -> None:
    """Layer-2: bounded evidence-fit scoring for newly unscored jobs."""
    from packages.core.services.role_profile import assess_fit_batch

    session: Session = SessionLocal()
    try:
        typer.echo(f"Assessing up to {limit} unscored jobs…")
        result = assess_fit_batch(
            session, limit=limit, role_filtered_only=role_filtered_only
        )
        session.commit()
        typer.echo(
            "Fit batch: "
            f"assessed={result['assessed']} "
            f"skipped_scored={result['skipped_scored']} "
            f"skipped_mismatch={result['skipped_mismatch']}"
        )
        for band, count in (result.get("bands") or {}).items():
            typer.echo(f"  {band}: {count}")
    finally:
        session.close()


@jobs_app.command(name="role-fit-report")
def role_fit_report_cmd() -> None:
    """Print Recommended / Stretch / Other roles counts and sample titles."""
    from packages.core.services.role_profile import feed_counts_snapshot, load_role_profile

    session: Session = SessionLocal()
    try:
        profile = load_role_profile(session)
        typer.echo(
            "Derived families: "
            + ", ".join(
                f"{f.label} ({f.id}{'' if f.enabled else ', disabled'})"
                for f in profile.families
            )
            or "(none)"
        )
        snap = feed_counts_snapshot(session)
        counts = snap["counts"]
        typer.echo(
            "Counts: "
            f"recommended_actionable={counts['recommended_actionable']} "
            f"stretch_actionable={counts['stretch_actionable']} "
            f"other_roles={counts['other_roles']} "
            f"unscored_fit={counts['unscored_fit']} "
            f"total={counts['total']}"
        )
        for key, titles in (snap.get("examples") or {}).items():
            typer.echo(f"Examples · {key}:")
            for title in titles:
                typer.echo(f"  · {title}")
    finally:
        session.close()


@jobs_app.command(name="reassess-eligibility")
def reassess_eligibility_cmd(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview eligibility counts without writing"
    ),
    limit: int | None = typer.Option(
        None, "--limit", help="Limit jobs reassessed (testing)"
    ),
) -> None:
    """Recompute eligibility for stored jobs (preserves applications and artifacts)."""
    from packages.core.services.job_eligibility import reassess_eligibility

    session: Session = SessionLocal()
    try:
        result = reassess_eligibility(session, dry_run=dry_run, limit=limit)
        if not dry_run:
            session.commit()
        typer.echo(
            "Eligibility reassessment: "
            f"scanned={result['scanned']}, dry_run={result['dry_run']}, "
            f"policy={result['policy_version']}, changed={result['changed']}, "
            f"stale_policy={result['stale_policy']}"
        )
        typer.echo("Results:")
        for key, count in (result.get("result_counts") or {}).items():
            typer.echo(f"  {key}: {count}")
        typer.echo("Exclusion / review reasons:")
        reasons = result.get("reason_counts") or {}
        if not reasons:
            typer.echo("  (none)")
        for key, count in reasons.items():
            typer.echo(f"  {key}: {count}")
    finally:
        session.close()


@jobs_app.command(name="enrich-descriptions")
def enrich_descriptions_cmd(
    dry_run: bool = typer.Option(
        True, "--dry-run/--apply", help="Preview by default; pass --apply to write"
    ),
    limit: int = typer.Option(25, "--limit", help="Max jobs to enrich this run"),
    recent_days: int | None = typer.Option(
        7, "--recent-days", help="Prefer stubs from the last N days (None = no cutoff)"
    ),
    job_id: Optional[List[str]] = typer.Option(
        None, "--job-id", help="Enrich specific job id(s); can repeat"
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-fetch even when description already looks full"
    ),
    eligible_fit_bands: str | None = typer.Option(
        None,
        "--eligible-fit-bands",
        help=(
            "Comma-separated evidence_fit bands to include "
            "(e.g. recommended,stretch). Skips excluded eligibility. "
            "Example: jobs enrich-descriptions --apply --limit 40 "
            "--eligible-fit-bands recommended,stretch"
        ),
    ),
) -> None:
    """Fetch full JDs for aggregator stubs (public GET only; bounded concurrency).

    Bounded enrichment for role-pass recommended/stretch stubs:
      jobs enrich-descriptions --apply --limit 40 --eligible-fit-bands recommended,stretch
    """
    from packages.core.services.job_enrichment import enrich_jobs_batch

    bands: set[str] | None = None
    if eligible_fit_bands:
        bands = {b.strip() for b in eligible_fit_bands.split(",") if b.strip()}

    session: Session = SessionLocal()
    try:
        result = enrich_jobs_batch(
            session,
            limit=limit,
            dry_run=dry_run,
            only_recent_days=recent_days,
            job_ids=[*job_id] if job_id else None,
            force=force,
            eligible_fit_bands=bands,
            skip_excluded=True,
        )
        if not dry_run:
            session.commit()
        data = result.to_dict()
        typer.echo(
            "JD enrichment: "
            f"dry_run={data['dry_run']} scanned={data['scanned']} "
            f"enriched={data['enriched']} unchanged={data['unchanged']} "
            f"skipped={data['skipped']} unavailable={data['unavailable']} "
            f"failed={data['failed']} title_drift={data['title_drift']}"
            + (f" fit_bands={sorted(bands)}" if bands else "")
        )
        for outcome in data.get("outcomes") or []:
            typer.echo(
                f"  {outcome['job_id'][:8]}… status={outcome['status']} "
                f"provider={outcome.get('provider')} "
                f"title_updated={outcome.get('title_updated')} "
                f"elig={outcome.get('eligibility_result')} "
                f"reason={outcome.get('reason')}"
            )
    finally:
        session.close()


@jobs_app.command(name="queue")
def queue_cmd(
    limit: int = 20,
    source: str | None = typer.Option(None, "--source", help="Filter by source_name"),
    referral_only: bool = typer.Option(False, "--referral-only"),
    target_only: bool = typer.Option(False, "--target-only"),
    min_score: int | None = typer.Option(None, "--min-score"),
    review: bool = typer.Option(False, "--review", help="Show needs-review jobs only"),
    excluded: bool = typer.Option(False, "--excluded", help="Show excluded jobs only"),
    all_eligibility: bool = typer.Option(
        False, "--all", help="Include actionable, review, and excluded jobs"
    ),
) -> None:
    """List ranked jobs for job search (actionable by default)."""
    from packages.core.services.job_eligibility import (
        ELIGIBILITY_ACTIONABLE,
        ELIGIBILITY_EXCLUDED,
        ELIGIBILITY_NEEDS_REVIEW,
    )
    from packages.core.services.job_display import format_queue_line_cli
    from packages.core.services.job_search_service import build_ranked_queue
    from packages.core.services.workflow_service import compute_job_lifecycle

    if review and excluded:
        typer.secho("Use only one of --review or --excluded.", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    eligibility_filter: str | None = ELIGIBILITY_ACTIONABLE
    if all_eligibility:
        eligibility_filter = None
    elif review:
        eligibility_filter = ELIGIBILITY_NEEDS_REVIEW
    elif excluded:
        eligibility_filter = ELIGIBILITY_EXCLUDED

    session: Session = SessionLocal()
    try:
        items = build_ranked_queue(
            session,
            limit=limit,
            source_name=source,
            referral_only=referral_only,
            target_only=target_only,
            min_score=min_score,
            eligibility=eligibility_filter,
        )
        if not items:
            label = eligibility_filter or "matching"
            typer.echo(f"No {label} jobs in queue.")
            return
        for item in items:
            lifecycle = compute_job_lifecycle(session, item.job.id)
            typer.echo(format_queue_line_cli(item, lifecycle=lifecycle))
            typer.echo(f"      reasons: {', '.join(item.ranked.reasons[:4])}")
    finally:
        session.close()


@jobs_app.command(name="queue-review")
def queue_review_cmd(limit: int = 20) -> None:
    """List ranked jobs that need manual review."""
    from packages.core.services.job_eligibility import ELIGIBILITY_NEEDS_REVIEW
    from packages.core.services.job_display import format_queue_line_cli
    from packages.core.services.job_search_service import build_ranked_queue

    session: Session = SessionLocal()
    try:
        items = build_ranked_queue(
            session, limit=limit, eligibility=ELIGIBILITY_NEEDS_REVIEW
        )
        if not items:
            typer.echo("No needs-review jobs.")
            return
        for item in items:
            typer.echo(format_queue_line_cli(item))
    finally:
        session.close()


@jobs_app.command(name="queue-excluded")
def queue_excluded_cmd(limit: int = 20) -> None:
    """List ranked excluded jobs for audit."""
    from packages.core.services.job_eligibility import ELIGIBILITY_EXCLUDED
    from packages.core.services.job_display import format_queue_line_cli
    from packages.core.services.job_search_service import build_ranked_queue

    session: Session = SessionLocal()
    try:
        items = build_ranked_queue(
            session, limit=limit, eligibility=ELIGIBILITY_EXCLUDED
        )
        if not items:
            typer.echo("No excluded jobs.")
            return
        for item in items:
            typer.echo(format_queue_line_cli(item))
    finally:
        session.close()


@jobs_app.command()
def triage() -> None:
    """List jobs queued for review."""
    session: Session = SessionLocal()
    try:
        qs = list_review_queue(session)
        for j in qs:
            typer.echo(f"{j.id} | {j.title[:50]} | {j.status}")
    finally:
        session.close()


@jobs_app.command()
def approve(job_id: str, confirm: bool = typer.Option(False, "--confirm")) -> None:
    """Approve a job posting for application."""
    if not confirm:
        typer.echo("This is a destructive action. Re-run with --confirm to proceed.")
        raise typer.Exit(code=2)
    session: Session = SessionLocal()
    try:
        j = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
        if j is None:
            typer.echo("Not found")
            raise typer.Exit(code=1)
        j.status = "approved"
        session.add(j)
        session.commit()
        typer.echo("Approved")
    finally:
        session.close()


@jobs_app.command()
def skip(job_id: str, confirm: bool = typer.Option(False, "--confirm")) -> None:
    """Skip a job posting."""
    if not confirm:
        typer.echo("This is a destructive action. Re-run with --confirm to proceed.")
        raise typer.Exit(code=2)
    session: Session = SessionLocal()
    try:
        j = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
        if j is None:
            typer.echo("Not found")
            raise typer.Exit(code=1)
        j.status = "skipped"
        session.add(j)
        session.commit()
        typer.echo("Skipped")
    finally:
        session.close()


@jobs_app.command()
def defer(job_id: str, until: str = typer.Option(..., help="YYYY-MM-DD")) -> None:
    """Defer review of a job posting until a specific date."""
    session: Session = SessionLocal()
    try:
        j = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
        if j is None:
            typer.echo("Not found")
            raise typer.Exit(code=1)
        _until = datetime.fromisoformat(until)
        j.status = "deferred"
        session.add(j)
        ev = AuditEvent(
            entity_type="job_posting",
            entity_id=j.id,
            event_type="deferred",
            payload={"until": until},
        )
        session.add(ev)
        session.commit()
        typer.echo("Deferred")
    finally:
        session.close()


@jobs_app.command(name="referral-start")
def referral_start_cmd(job_id: str, cutoff_hours: int = 48) -> None:
    """Start a referral task for a job posting with a 48-hour cutoff."""
    session: Session = SessionLocal()
    try:
        try:
            rt = start_referral(session, job_id, cutoff_hours=cutoff_hours)
            session.commit()
        except Exception as exc:
            from packages.core.services.job_service import JobNotFoundError

            if isinstance(exc, JobNotFoundError):
                typer.echo(f"Job not found: {job_id}")
                raise typer.Exit(code=1)
            # re-raise unexpected exceptions
            raise

        # Attempt to find a linked decision request created by start_referral
        from packages.core.domain.models import DecisionRequest

        dec = (
            session.query(DecisionRequest)
            .filter(DecisionRequest.idempotency_key == f"referral:{rt.id}")
            .one_or_none()
        )
        if dec:
            typer.echo(
                f"Referral task started, cutoff_at={rt.cutoff_at}, decision_id={dec.id}"
            )
        else:
            typer.echo(f"Referral task started, cutoff_at={rt.cutoff_at}")
    finally:
        session.close()


@jobs_app.command(name="referral-status")
def referral_status_cmd(job_id: str) -> None:
    """Check the status of a referral task."""
    session: Session = SessionLocal()
    try:
        r = (
            session.query(ReferralTask)
            .filter(ReferralTask.job_posting_id == job_id)
            .one_or_none()
        )
        if r is None:
            typer.echo("No referral task")
            raise typer.Exit(code=1)
        typer.echo(f"status={r.status}, cutoff_at={r.cutoff_at}")
    finally:
        session.close()
