from __future__ import annotations

from pathlib import Path

from apps.resume_generation.services.resume_corpus_service import discover_docx_files
from tests.resume_generation.conftest import build_standard_corpus


def test_discovery_ignores_pdf_temp_and_non_docx(tmp_path: Path) -> None:
    root = build_standard_corpus(tmp_path / "resumes")
    files, summary = discover_docx_files(root)
    names = {p.name for p in files}
    assert "Current_Resume_Dev.docx" in names
    assert "Acme_Tailored.docx" in names
    assert "ignore_me.pdf" not in names
    assert "~$Current_Resume_Dev.docx" not in names
    assert "notes.txt" not in names
    assert summary.discovered == len(files)
    assert summary.skipped >= 3
    cover = root / "Cover Letters" / "Alex_Michael_Manjakunnel_Cover.docx"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"not-a-real-docx")
    files_after, _summary_after = discover_docx_files(root)
    assert cover.name not in {path.name for path in files_after}
