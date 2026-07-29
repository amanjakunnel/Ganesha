from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from packages.core.domain.models import JobPosting, ReferralContact
from packages.core.services.company_normalize import companies_likely_same, is_target_company, normalize_company_key
from packages.core.services.job_ranking import RankedJob, rank_job


@dataclass
class RankedQueueItem:
    ranked: RankedJob
    job: JobPosting
    company_name: str
    source_name: str | None
    has_referral_contacts: bool
    referral_contact_count: int
    is_target_company: bool
    target_company_name: str | None


def _referral_index(session: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rc in session.query(ReferralContact).all():
        key = normalize_company_key(rc.company_name_raw)
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def _referral_count_for_job(job: JobPosting, index: dict[str, int]) -> int:
    if not job.company:
        return 0
    key = job.company.normalized_key or normalize_company_key(job.company.canonical_name)
    if key in index:
        return index[key]
    for k, v in index.items():
        if companies_likely_same(k, key):
            return v
    return 0


def build_ranked_queue(
    session: Session,
    *,
    limit: int = 30,
    source_name: str | None = None,
    referral_only: bool = False,
    target_only: bool = False,
    min_score: int | None = None,
) -> list[RankedQueueItem]:
    referral_index = _referral_index(session)
    q = session.query(JobPosting).filter(
        JobPosting.status.in_(["new", "queued_for_review", "assessed", "needs_decision"])
    )
    if source_name:
        q = q.filter(JobPosting.source_name == source_name)
    jobs = q.all()
    items: list[RankedQueueItem] = []
    for job in jobs:
        company_name = job.company.canonical_name if job.company else "Unknown"
        ref_count = _referral_count_for_job(job, referral_index)
        has_ref = ref_count > 0
        is_target, target_name = is_target_company(company_name)
        if referral_only and not has_ref:
            continue
        if target_only and not is_target:
            continue
        ranked = rank_job(
            job_id=job.id,
            title=job.title,
            company_name=company_name,
            description=job.description_text or "",
            source_name=job.source_name,
            posted_at=job.posted_at,
            scraped_at=job.scraped_at,
            has_referral_contacts=has_ref,
            referral_contact_count=ref_count,
            status=job.status,
            intake_metadata=job.intake_metadata,
        )
        if min_score is not None and ranked.score < min_score:
            continue
        items.append(
            RankedQueueItem(
                ranked=ranked,
                job=job,
                company_name=company_name,
                source_name=job.source_name,
                has_referral_contacts=has_ref,
                referral_contact_count=ref_count,
                is_target_company=is_target,
                target_company_name=target_name,
            )
        )
    items.sort(key=lambda x: x.ranked.score, reverse=True)
    return items[:limit]


def job_detail_with_ranking(session: Session, job_id: str) -> dict[str, Any]:
    job = session.query(JobPosting).filter(JobPosting.id == job_id).one_or_none()
    if job is None:
        raise LookupError(job_id)
    referral_index = _referral_index(session)
    company_name = job.company.canonical_name if job.company else "Unknown"
    ref_count = _referral_count_for_job(job, referral_index)
    is_target, target_name = is_target_company(company_name)
    ranked = rank_job(
        job_id=job.id,
        title=job.title,
        company_name=company_name,
        description=job.description_text or "",
        source_name=job.source_name,
        posted_at=job.posted_at,
        scraped_at=job.scraped_at,
        has_referral_contacts=ref_count > 0,
        referral_contact_count=ref_count,
        status=job.status,
        intake_metadata=job.intake_metadata,
    )
    contacts: list[ReferralContact] = []
    if job.company:
        key = job.company.normalized_key or normalize_company_key(company_name)
        contacts = [
            c
            for c in session.query(ReferralContact).all()
            if companies_likely_same(c.company_name_raw, company_name)
            or normalize_company_key(c.company_name_raw) == key
        ]
    return {
        "job": job,
        "company_name": company_name,
        "rank_score": ranked.score,
        "rank_reasons": ranked.reasons,
        "rank_flags": ranked.flags,
        "is_target_company": is_target,
        "target_company_name": target_name,
        "referral_contact_count": ref_count,
        "referral_contacts": contacts,
    }
