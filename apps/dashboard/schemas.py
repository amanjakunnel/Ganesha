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
    all_eligible = "all_eligible"
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
    eligibility: str | None = None


class SourceOption(BaseModel):
    value: str
    label: str
    count: int


class FitBandCounts(BaseModel):
    recommended: int = 0
    stretch: int = 0
    needs_review: int = 0
    low_fit: int = 0
    unscored: int = 0
    all_eligible: int = 0


class JobsListResponse(BaseModel):
    items: list[JobDashboardItem]
    page: int
    page_size: int
    total: int
    total_pages: int
    available_sources: list[SourceOption]
    fit_counts: FitBandCounts = Field(default_factory=FitBandCounts)
    fit_filter: str = "recommended"


class JobQueryParams(BaseModel):
    q: str | None = None
    source: str | None = None
    location: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    fit: FitFeedFilter = FitFeedFilter.recommended
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
