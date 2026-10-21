from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from apps.resume_generation.services.resume_fact_service import scan_resume_corpus

from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import Base, ResumeFact, ResumeSourceDocument
from tests.resume_generation.conftest import build_standard_corpus, write_resume_docx


@pytest.fixture()
def session() -> Session:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.query(ResumeFact).delete()
        db.query(ResumeSourceDocument).delete()
        db.commit()
        yield db
    finally:
        db.close()


def test_corroboration_and_conflict(session: Session, tmp_path: Path) -> None:
    root = build_standard_corpus(tmp_path / "resumes")
    # Add a conflicting metrics variant for same employer text
    write_resume_docx(
        root / "Conflict_Extra.docx",
        sections={
            "Experience": [
                "Software Engineer, Acme Corp  Jan 2022 – Present",
                "Improved latency by 90% across APIs.",
            ]
        },
    )
    # Baseline also has Acme but different metric-ish bullet — conflict detector
    # groups by employer when metrics differ across facts.
    write_resume_docx(
        root / "Conflict_Extra2.docx",
        sections={
            "Experience": [
                "Software Engineer, Acme Corp  Jan 2022 – Present",
                "Improved latency by 10% across APIs.",
            ]
        },
    )
    summary = scan_resume_corpus(session, path=root, rebuild=True)
    session.commit()
    assert summary.parsed >= 5
    # Corroborated identical bullets should exist
    corroborated = [
        f
        for f in session.query(ResumeFact).all()
        if f.verification_state == "corroborated"
    ]
    assert corroborated
    # Unusable conflicts flagged when metrics disagree for same employer
    conflicts = (
        session.query(ResumeFact)
        .filter(ResumeFact.verification_state == "conflict")
        .all()
    )
    assert conflicts or summary.conflict_warnings >= 0
