from __future__ import annotations

import fcntl
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from pypdf import PdfWriter

from apps.resume_generation.services.ats_validation_service import atomic_copy_to_desktop
from apps.resume_generation.services.pdf_render_service import (
    PdfEngineStatus,
    _convert_with_word,
    convert_docx_to_pdf,
)

# fcntl used by concurrency lock test


def _write_blank_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        writer.write(fh)


def test_word_conversion_stages_through_stable_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "word_workspace"
    monkeypatch.setattr(
        "apps.resume_generation.services.pdf_render_service.WORD_CONVERSION_ROOT",
        workspace,
    )
    monkeypatch.setattr(
        "apps.resume_generation.services.pdf_render_service._WORD_LOCK_PATH",
        workspace / ".convert.lock",
    )
    src_docx = tmp_path / "job" / "stamp" / "resume.docx"
    dest_pdf = tmp_path / "job" / "stamp" / "resume.pdf"
    src_docx.parent.mkdir(parents=True)
    src_docx.write_bytes(b"PK-fake-docx-bytes")

    def fake_run(cmd, **_kwargs):
        script = cmd[2]
        assert "tell application \"Finder\"" in script
        assert str(src_docx) not in script  # final job path must not be opened by Word
        work_files = list(workspace.glob("_ganesha_convert_*.docx"))
        assert work_files
        _write_blank_pdf(work_files[0].with_suffix(".pdf"))

        class Result:
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(
        "apps.resume_generation.services.pdf_render_service.resolve_word_workspace",
        lambda: workspace,
    )
    with patch("subprocess.run", side_effect=fake_run):
        _convert_with_word(src_docx, dest_pdf)

    assert dest_pdf.exists() and dest_pdf.stat().st_size > 0
    assert list(workspace.glob("_ganesha_convert_*.docx")) == []
    assert list(workspace.glob("_ganesha_convert_*.pdf")) == []


def test_word_timeout_leaves_prior_desktop_pdf_intact(
    tmp_path: Path, monkeypatch
) -> None:
    desktop = tmp_path / "Alex_Michael_Manjakunnel_Resume.pdf"
    _write_blank_pdf(desktop)
    before = desktop.read_bytes()

    workspace = tmp_path / "word_workspace"
    monkeypatch.setattr(
        "apps.resume_generation.services.pdf_render_service.WORD_CONVERSION_ROOT",
        workspace,
    )
    monkeypatch.setattr(
        "apps.resume_generation.services.pdf_render_service._WORD_LOCK_PATH",
        workspace / ".convert.lock",
    )

    def boom(*_a, **_k):
        raise TimeoutError("simulated")

    docx = tmp_path / "in.docx"
    pdf = tmp_path / "out.pdf"
    docx.write_bytes(b"PK")
    with patch(
        "apps.resume_generation.services.pdf_render_service.detect_pdf_engine",
        return_value=PdfEngineStatus(True, False, "microsoft_word", "mock"),
    ):
        with patch(
            "apps.resume_generation.services.pdf_render_service._convert_with_word",
            side_effect=RuntimeError("Word PDF conversion timed out after 120s"),
        ):
            with pytest.raises(RuntimeError, match="timed out"):
                convert_docx_to_pdf(docx, pdf)
    assert desktop.read_bytes() == before
    with pytest.raises(RuntimeError):
        atomic_copy_to_desktop(tmp_path / "missing.pdf", desktop)
    assert desktop.read_bytes() == before


def test_describe_workspace_exposes_fallback_mode(tmp_path: Path, monkeypatch) -> None:
    from apps.resume_generation.services.pdf_render_service import (
        describe_word_workspace,
        mark_word_workspace_authorized,
    )

    workspace = tmp_path / "word_workspace"
    monkeypatch.setattr(
        "apps.resume_generation.services.pdf_render_service.WORD_CONVERSION_ROOT",
        workspace,
    )
    monkeypatch.setattr(
        "apps.resume_generation.services.pdf_render_service._WORD_AUTH_MARKER",
        workspace / ".authorized",
    )
    monkeypatch.setattr(
        "apps.resume_generation.services.pdf_render_service._recent_authorized_job_dir",
        lambda: tmp_path / "old_stamp",
    )
    (tmp_path / "old_stamp").mkdir()
    choice = describe_word_workspace(allow_fallback=True)
    assert choice.mode == "fallback_stamp"
    assert "TRANSITIONAL" in choice.detail
    mark_word_workspace_authorized()
    permanent = describe_word_workspace(allow_fallback=True)
    assert permanent.mode == "permanent"
    assert permanent.path == workspace


def test_concurrent_word_conversions_do_not_overlap_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    """Flock must serialize Word workspace use across callers."""
    workspace = tmp_path / "word_workspace"
    lock_path = workspace / ".convert.lock"
    workspace.mkdir(parents=True)
    active = 0
    max_active = 0
    gate = threading.Lock()
    errors: list[BaseException] = []

    def worker(_i: int) -> None:
        nonlocal active, max_active
        try:
            lock_path.touch(exist_ok=True)
            with lock_path.open("a+", encoding="utf-8") as lock_fh:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                try:
                    with gate:
                        active += 1
                        max_active = max(max_active, active)
                    time.sleep(0.05)
                    with gate:
                        active -= 1
                finally:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert max_active == 1
