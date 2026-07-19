"""SQLAlchemy domain models for job intake and management.

Uses SQLAlchemy 2.x typed declarative style with Mapped types for full type safety.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    JSON,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """SQLAlchemy declarative base with type hints."""

    pass


def make_uuid() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())


class Company(Base):
    """Company or employer entity."""

    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    canonical_name: Mapped[str] = mapped_column(String(255), unique=True)
    website_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JobPosting(Base):
    """A job posting discovered or manually entered."""

    __tablename__ = "job_postings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    source: Mapped[str] = mapped_column(String(32))  # manual, csv_import, json_import
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    title: Mapped[str] = mapped_column(String(512))
    company_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("companies.id"), nullable=True
    )
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workplace_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description_text: Mapped[str] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    application_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default="new"
    )  # new, queued_for_review, approved, deferred, skipped, applied_manually, archived
    normalized_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description_hash: Mapped[str] = mapped_column(String(128))
    dedupe_key: Mapped[str] = mapped_column(String(128))
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    company: Mapped[Company | None] = relationship("Company")

    __table_args__ = (
        Index("ix_job_dedupe_key", "dedupe_key"),
        Index("ix_job_description_hash", "description_hash"),
    )


class JobAssessment(Base):
    """Assessment of a job posting for suitability and track recommendation."""

    __tablename__ = "job_assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    job_posting_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("job_postings.id"), unique=True
    )
    recommended_track: Mapped[str] = mapped_column(String(32))
    score: Mapped[int] = mapped_column(Integer)
    score_explanation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    key_skills: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    missing_or_uncertain_skills: Mapped[list[str] | None] = mapped_column(
        JSON, nullable=True
    )
    manual_review_reasons: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    job: Mapped[JobPosting] = relationship("JobPosting")


class ResumeProfile(Base):
    """A configured resume track (ml, cloud, dev) and its metadata."""

    __tablename__ = "resume_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    track: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(255))
    source_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    active: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReferralTask(Base):
    """Tracks referral opportunities and 48-hour cutoff."""

    __tablename__ = "referral_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    job_posting_id: Mapped[str] = mapped_column(String(36), ForeignKey("job_postings.id"))
    status: Mapped[str] = mapped_column(
        String(32), default="not_needed"
    )  # not_needed, research_needed, draft_ready, sent_manually, waiting, cutoff_reached, closed
    cutoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    job: Mapped[JobPosting] = relationship("JobPosting")


class AuditEvent(Base):
    """Append-only audit trail of all state changes and operations."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=make_uuid)
    entity_type: Mapped[str] = mapped_column(String(128))
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_type: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# Helper functions for deduplication and hashing


def hash_text(s: str) -> str:
    """Hash text using SHA-256."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def make_description_hash(text: str) -> str:
    """Hash job description for duplicate detection."""
    return hash_text(text or "")


def make_dedupe_key(
    company_name: str | None, title: str | None, location: str | None
) -> str:
    """Generate deduplication key from company, title, and location."""
    key = "|".join([company_name or "", (title or "").lower().strip(), (location or "")])
    return hash_text(key)
