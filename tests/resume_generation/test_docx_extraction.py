from __future__ import annotations

from pathlib import Path

from apps.resume_generation.services.resume_corpus_service import (
    extract_facts_from_docx,
    file_content_hash,
)
from tests.resume_generation.conftest import build_standard_corpus, write_resume_docx


def test_extraction_preserves_sections_and_provenance(tmp_path: Path) -> None:
    root = build_standard_corpus(tmp_path / "resumes")
    path = root / "Current_Resume_Dev.docx"
    facts = extract_facts_from_docx(path, root)
    sections = {f.section for f in facts}
    assert "skills" in sections
    assert "experience" in sections
    assert "projects" in sections
    assert "education" in sections
    assert any("TacoDB" in f.source_text for f in facts)
    assert all(f.content_hash and f.normalized_text for f in facts)


def test_content_hash_detects_change(tmp_path: Path) -> None:
    path = write_resume_docx(
        tmp_path / "r.docx",
        sections={"Skills": ["Python"]},
    )
    h1 = file_content_hash(path)
    write_resume_docx(path, sections={"Skills": ["Python", "Java"]})
    h2 = file_content_hash(path)
    assert h1 != h2
