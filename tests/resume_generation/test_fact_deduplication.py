from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from apps.resume_generation.services.resume_fact_service import scan_resume_corpus
from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import Base, ResumeFact, ResumeSourceDocument
from tests.resume_generation.conftest import build_conflicting_gpa_corpus, build_standard_corpus


@pytest.fixture()
def session() -> Session:
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.query(ResumeFact).delete()
        db.query(ResumeSourceDocument).delete()
        db.commit()
        yield db
    finally:
        db.close()


def test_gpa_conflicts_detected_and_unusable(session: Session, tmp_path: Path) -> None:
    root = build_conflicting_gpa_corpus(tmp_path / "resumes")
    summary = scan_resume_corpus(session, path=root, rebuild=True)
    session.commit()
    assert summary.conflict_warnings >= 1
    gpa_facts = (
        session.query(ResumeFact)
        .filter(ResumeFact.gpa_value.isnot(None))
        .all()
    )
    assert {f.gpa_value for f in gpa_facts} >= {"3.5", "3.6", "3.7"}
    assert all(f.verification_state == "conflict" for f in gpa_facts)
    assert all(f.usable is False for f in gpa_facts)


def test_corroboration_for_identical_bullets(session: Session, tmp_path: Path) -> None:
    root = build_standard_corpus(tmp_path / "resumes")
    summary = scan_resume_corpus(session, path=root, rebuild=True)
    session.commit()
    assert summary.parsed >= 4
    corroborated = [
        f
        for f in session.query(ResumeFact).all()
        if f.verification_state == "corroborated"
    ]
    assert corroborated
