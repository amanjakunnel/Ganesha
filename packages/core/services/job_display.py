from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any

from packages.core.domain.models import JobPosting
from packages.core.services.job_eligibility import (
    reason_display_label,
    resolve_job_eligibility,
)
from packages.core.services.job_search_service import RankedQueueItem


SOURCE_DISPLAY_LABELS: dict[str, str] = {
    "linkedin": "linkedin_import",
    "linkedin_import": "linkedin_import",
    "symplicity": "symplicity_import",
    "symplicity_import": "symplicity_import",
    "simplifyjobs": "simplifyjobs",
    "manual": "manual",
}


def source_display_label(source_name: str | None) -> str:
    if not source_name:
        return "unknown"
    return SOURCE_DISPLAY_LABELS.get(source_name.lower(), source_name)


def freshness_label(job: JobPosting) -> str:
    ref = job.scraped_at or job.posted_at or job.discovered_at
    if not ref:
        return "freshness unknown"
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)
    age_days = max(0, (datetime.now(UTC) - ref).days)
    if age_days == 0:
        return "today"
    if age_days == 1:
        return "1d"
    if age_days < 7:
        return f"{age_days}d"
    if age_days < 30:
        return f"{age_days // 7}w"
    return f"{age_days}d"


def format_queue_line_cli(
    item: RankedQueueItem, *, lifecycle: str | None = None
) -> str:
    job = item.job
    eligibility = resolve_job_eligibility(job)
    flags = []
    if item.is_target_company:
        flags.append("target")
    if item.has_referral_contacts:
        flags.append(f"referral:{item.referral_contact_count}")
    flag_txt = f" [{' '.join(flags)}]" if flags else ""
    reason_txt = ""
    if eligibility.reason_category:
        reason_txt = f" | {reason_display_label(eligibility.reason_category)}"
    apply_hint = " | apply=yes" if job.company_apply_url else " | apply=no"
    lifecycle_txt = f" | lifecycle={lifecycle}" if lifecycle else ""
    return (
        f"{item.ranked.score:>4} | {job.id} | {source_display_label(item.source_name)} | "
        f"{item.company_name} | {job.title[:50]}{flag_txt}\n"
        f"      fresh={freshness_label(job)} | {eligibility.result}{reason_txt}{apply_hint}{lifecycle_txt}"
    )


def format_queue_line_telegram(item: RankedQueueItem) -> str:
    job = item.job
    title = escape(job.title[:48])
    company = escape(item.company_name[:32])
    src = escape(source_display_label(item.source_name))
    fresh = escape(freshness_label(job))
    line = f"• <code>{escape(job.id)}</code> [{src}|{fresh}] {company}: {title}"
    if job.company_apply_url:
        line += " 🔗"
    return line


def format_job_detail_cli(
    detail: dict[str, Any], *, lifecycle: str | None = None
) -> str:
    jp: JobPosting = detail["job"]
    eligibility = detail.get("eligibility") or resolve_job_eligibility(jp)
    lines = [
        f"ID: {jp.id}",
        f"Title: {jp.title}",
        f"Company: {detail['company_name']}",
        f"Source: {source_display_label(jp.source_name)} ({jp.source_type or '-'})",
        f"Location: {jp.location or '-'}",
        f"Freshness: {freshness_label(jp)}",
        f"Status: {jp.status}",
    ]
    if lifecycle:
        lines.append(f"Lifecycle: {lifecycle}")
    lines.extend(
        [
            f"Eligibility: {eligibility.result}",
            f"Rank score: {detail['rank_score']}",
            f"Rank reasons: {', '.join(detail['rank_reasons'][:6])}",
        ]
    )
    if eligibility.reason_category:
        lines.append(
            f"Review reason: {reason_display_label(eligibility.reason_category)}"
        )
    if detail["is_target_company"]:
        lines.append(f"Target company: {detail['target_company_name']}")
    if detail["referral_contact_count"]:
        lines.append(f"Referral contacts: {detail['referral_contact_count']}")
    if jp.company_apply_url:
        lines.append(f"Apply URL: {jp.company_apply_url}")
    if jp.canonical_url:
        lines.append(f"Source row: {jp.canonical_url}")
    return "\n".join(lines)


def format_job_detail_telegram(
    detail: dict[str, Any], *, lifecycle: str | None = None
) -> str:
    jp: JobPosting = detail["job"]
    eligibility = detail.get("eligibility") or resolve_job_eligibility(jp)
    lines = [
        f"<b>{escape(jp.title)}</b>",
        f"Company: {escape(detail['company_name'])}",
        f"ID: <code>{escape(jp.id)}</code>",
        f"Source: {escape(source_display_label(jp.source_name))} | fresh: {escape(freshness_label(jp))}",
    ]
    if jp.location:
        lines.append(f"Location: {escape(jp.location)}")
    if lifecycle:
        lines.append(f"Lifecycle: {escape(lifecycle)}")
    lines.append(f"Eligibility: {escape(eligibility.result)}")
    if eligibility.reason_category:
        lines.append(
            f"Note: {escape(reason_display_label(eligibility.reason_category))}"
        )
    lines.append(f"Rank: {detail['rank_score']}")
    if jp.company_apply_url:
        lines.append(
            f'<a href="{escape(jp.company_apply_url, quote=True)}">Apply link</a>'
        )
    return "\n".join(lines)


def format_sources_summary_telegram(sources: list[dict[str, Any]]) -> str:
    lines = ["<b>Job sources</b>"]
    for src in sources:
        label = escape(str(src.get("label", "unknown")))
        status = escape(str(src.get("status", "unknown")))
        count = src.get("job_count", 0)
        last = src.get("last_sync") or "never"
        lines.append(
            f"• {label}: {count} jobs | last sync {escape(str(last))} | {status}"
        )
    lines.append("")
    lines.append(
        "SimplifyJobs sync: run locally with <code>jobs sync-simplifyjobs</code>"
    )
    return "\n".join(lines)
