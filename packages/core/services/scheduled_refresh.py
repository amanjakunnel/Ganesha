"""Opt-in scheduled refresh pipeline (default off).

Runs: source refresh → Layer-1 classify + Layer-2 score for new jobs →
enrich ready-to-apply candidates. Never opens browsers, fills forms, or
submits applications. Logs under ~/.ganesha/logs/.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

LOG_DIR = Path.home() / ".ganesha" / "logs"
DEFAULT_SCORE_LIMIT = 50
DEFAULT_ENRICH_LIMIT = 25


@dataclass
class ScheduledRefreshResult:
    started_at: str
    finished_at: str | None = None
    refresh: dict[str, Any] = field(default_factory=dict)
    classify: dict[str, Any] = field(default_factory=dict)
    score: dict[str, Any] = field(default_factory=dict)
    enrich: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False
    opened_browser: bool = False
    submitted_application: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_scheduled_refresh_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Flag parsing for launchd/cron wrappers and unit tests."""
    parser = argparse.ArgumentParser(
        prog="scheduled-refresh",
        description=(
            "Opt-in refresh + score + enrich. Never opens browsers or submits."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan steps without writing sync/score/enrich results",
    )
    parser.add_argument(
        "--skip-refresh",
        action="store_true",
        help="Skip SimplifyJobs / board sync",
    )
    parser.add_argument(
        "--skip-score",
        action="store_true",
        help="Skip Layer-1 classify + Layer-2 fit scoring",
    )
    parser.add_argument(
        "--skip-enrich",
        action="store_true",
        help="Skip enrichment of ready-to-apply candidates",
    )
    parser.add_argument(
        "--score-limit",
        type=int,
        default=DEFAULT_SCORE_LIMIT,
        help=f"Max jobs to score (default {DEFAULT_SCORE_LIMIT})",
    )
    parser.add_argument(
        "--enrich-limit",
        type=int,
        default=DEFAULT_ENRICH_LIMIT,
        help=f"Max ready-to-apply jobs to enrich (default {DEFAULT_ENRICH_LIMIT})",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default=str(LOG_DIR),
        help="Log directory (default ~/.ganesha/logs)",
    )
    return parser.parse_args(argv)


def _append_log(payload: dict[str, Any], log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        log_dir.chmod(0o700)
    except OSError:
        pass
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = log_dir / f"scheduled-refresh-{stamp}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    # Also append a one-line summary for tailing.
    summary = log_dir / "scheduled-refresh.log"
    line = (
        f"{payload.get('started_at')} finished={payload.get('finished_at')} "
        f"errors={len(payload.get('errors') or [])} "
        f"dry_run={payload.get('dry_run')}\n"
    )
    with summary.open("a", encoding="utf-8") as fh:
        fh.write(line)
    try:
        summary.chmod(0o600)
    except OSError:
        pass
    return path


def run_scheduled_refresh(
    session: Session,
    *,
    dry_run: bool = False,
    skip_refresh: bool = False,
    skip_score: bool = False,
    skip_enrich: bool = False,
    score_limit: int = DEFAULT_SCORE_LIMIT,
    enrich_limit: int = DEFAULT_ENRICH_LIMIT,
    log_dir: Path | str = LOG_DIR,
) -> ScheduledRefreshResult:
    """Execute the opt-in pipeline. Never opens browsers or submits apps."""
    result = ScheduledRefreshResult(
        started_at=datetime.now(UTC).isoformat(),
        dry_run=dry_run,
        opened_browser=False,
        submitted_application=False,
    )

    if not skip_refresh:
        try:
            if dry_run:
                result.refresh = {
                    "skipped": False,
                    "dry_run": True,
                    "note": "Would sync SimplifyJobs + company boards",
                }
            else:
                from packages.core.services.job_refresh_service import start_refresh
                import time

                state, code = start_refresh(force=True)
                # Wait briefly for background thread to finish (bounded).
                deadline = time.time() + 540
                while time.time() < deadline and state.status == "running":
                    time.sleep(1.0)
                result.refresh = {
                    "http_status_hint": code,
                    **state.to_dict(),
                }
        except Exception as exc:
            result.errors.append(f"refresh:{exc.__class__.__name__}:{exc}")
            result.refresh = {"failed": True, "error": str(exc)}

    if not skip_score:
        try:
            from packages.core.services.role_profile import (
                assess_fit_batch,
                classify_jobs_batch,
            )

            classify = classify_jobs_batch(
                session, limit=score_limit, apply=not dry_run, only_missing=True
            )
            score = assess_fit_batch(
                session, limit=score_limit, role_filtered_only=True
            )
            if not dry_run:
                session.flush()
            result.classify = classify
            result.score = score
        except Exception as exc:
            result.errors.append(f"score:{exc.__class__.__name__}:{exc}")
            result.score = {"failed": True, "error": str(exc)}

    if not skip_enrich:
        try:
            from packages.core.domain.models import JobApplication, JobPosting
            from packages.core.services.job_enrichment import enrich_job, job_needs_enrichment

            apps = (
                session.query(JobApplication)
                .filter(JobApplication.status == "ready_to_apply")
                .limit(max(enrich_limit * 3, enrich_limit))
                .all()
            )
            enriched = 0
            skipped = 0
            for app in apps:
                if enriched >= enrich_limit:
                    break
                job = (
                    session.query(JobPosting)
                    .filter(JobPosting.id == app.job_posting_id)
                    .one_or_none()
                )
                if job is None or not job_needs_enrichment(job):
                    skipped += 1
                    continue
                if dry_run:
                    enriched += 1
                    continue
                outcome = enrich_job(session, job, dry_run=False)
                if outcome.status in {"enriched", "unchanged"}:
                    enriched += 1
                else:
                    skipped += 1
            if not dry_run:
                session.flush()
            result.enrich = {
                "enriched": enriched,
                "skipped": skipped,
                "limit": enrich_limit,
                "dry_run": dry_run,
                "note": "ready_to_apply candidates only; no browser/submit",
            }
        except Exception as exc:
            result.errors.append(f"enrich:{exc.__class__.__name__}:{exc}")
            result.enrich = {"failed": True, "error": str(exc)}

    result.finished_at = datetime.now(UTC).isoformat()
    log_path = _append_log(result.to_dict(), Path(log_dir))
    result.refresh.setdefault("log_path", str(log_path))
    return result
