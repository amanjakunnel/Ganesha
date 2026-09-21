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


class SourceOption(BaseModel):
    value: str
    label: str
    count: int


class JobsListResponse(BaseModel):
    items: list[JobDashboardItem]
    page: int
    page_size: int
    total: int
    total_pages: int
    available_sources: list[SourceOption]


class JobQueryParams(BaseModel):
    q: str | None = None
    source: str | None = None
    location: str | None = None
    date_from: date | None = None
    date_to: date | None = None
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
