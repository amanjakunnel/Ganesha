from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class JobSort(str, Enum):
    date_desc = "date_desc"
    date_asc = "date_asc"
    title_asc = "title_asc"
    title_desc = "title_desc"
    company_asc = "company_asc"
    company_desc = "company_desc"
    location_asc = "location_asc"
    location_desc = "location_desc"
    source_asc = "source_asc"
    source_desc = "source_desc"
    fit_desc = "fit_desc"


class FitFeedFilter(str, Enum):
    recommended = "recommended"
    recommended_stretch = "recommended_stretch"
    needs_review = "needs_review"
    low_fit = "low_fit"
    other_roles = "other_roles"
    all_eligible = "all_eligible"
    all = "all"


class RecencyFilter(str, Enum):
    hours_24 = "24h"
    hours_48 = "48h"
    days_7 = "7d"
    days_30 = "30d"
    all = "all"


class JobDashboardItem(BaseModel):
    id: str
    title: str
    company: str
    location: str
    source: str
    source_label: str
    source_type: str | None
    date_found: datetime | None
    posted_at: datetime | None
    created_at: datetime | None
    job_url: str | None
    status: str
    fit_band: str | None = None
    fit_score: int | None = None
    fit_reason: str | None = None
    fit_confidence: str | None = None
    role_family_status: str | None = None
    role_matched_family: str | None = None
    eligibility: str | None = None
    eligibility_reason: str | None = None
    eligibility_flags: list[str] = Field(default_factory=list)
    description_verified: bool = True
    description_badge: str | None = None
    recency_at: datetime | None = None
    recency_label: str | None = None
    first_seen_at: datetime | None = None
    is_new_since_refresh: bool = False


class SourceOption(BaseModel):
    value: str
    label: str
    count: int


class FitBandCounts(BaseModel):
    recommended: int = 0
    stretch: int = 0
    needs_review: int = 0
    low_fit: int = 0
    other_roles: int = 0
    unscored: int = 0
    all_eligible: int = 0


class RecencyCounts(BaseModel):
    hours_24: int = 0
    hours_48: int = 0
    days_7: int = 0
    days_30: int = 0
    all: int = 0
    unknown_or_approx: int = 0


class JobsListResponse(BaseModel):
    items: list[JobDashboardItem]
    page: int
    page_size: int
    total: int
    total_pages: int
    available_sources: list[SourceOption]
    fit_counts: FitBandCounts = Field(default_factory=FitBandCounts)
    fit_filter: str = "recommended"
    recency_filter: str = "all"
    recency_counts: RecencyCounts = Field(default_factory=RecencyCounts)
    last_refreshed: datetime | None = None
    is_stale: bool = False
    stale_threshold_hours: int = 24


class JobQueryParams(BaseModel):
    q: str | None = None
    source: str | None = None
    location: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    fit: FitFeedFilter = FitFeedFilter.recommended
    recency: RecencyFilter = RecencyFilter.all
    sort: JobSort = JobSort.date_desc
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)

    @field_validator("q", "source", "location", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value
