from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from apps.cli.main import app
from apps.resume_generation.schemas import GenerationResult, ScanSummary


runner = CliRunner()


def test_resumes_doctor() -> None:
    result = runner.invoke(app, ["resumes", "doctor"])
    assert result.exit_code == 0
    assert "Resume doctor" in result.stdout
    assert "Semantic provider" in result.stdout
    assert "API key" in result.stdout
    # Never print secret values
    assert "sk-" not in result.stdout


def test_resumes_scan_dry_run(tmp_path: Path) -> None:
    summary = ScanSummary(discovered=2, parsed=2, skipped=1)
    with patch(
        "apps.cli.resume_commands.scan_resume_corpus",
        return_value=summary,
    ):
        with patch("apps.cli.resume_commands.SessionLocal") as sl:
            sl.return_value = MagicMock()
            result = runner.invoke(
                app,
                ["resumes", "scan", "--path", str(tmp_path), "--dry-run"],
            )
    assert result.exit_code == 0
    assert "Discovered: 2" in result.stdout
    assert "dry-run" in result.stdout


def test_resumes_generate_flags() -> None:
    gen = GenerationResult(
        status="dry_run",
        track="dev",
        source_files=["Current_Resume_Dev.docx"],
        provider="deterministic",
    )
    with patch(
        "apps.cli.resume_commands.generate_resume_for_job",
        return_value=gen,
    ) as mocked:
        with patch("apps.cli.resume_commands.SessionLocal") as sl:
            session = MagicMock()
            sl.return_value = session
            result = runner.invoke(
                app,
                [
                    "resumes",
                    "generate",
                    "job-123",
                    "--track",
                    "dev",
                    "--deterministic-only",
                    "--dry-run",
                    "--no-desktop-copy",
                ],
            )
    assert result.exit_code == 0
    assert "Status: dry_run" in result.stdout
    kwargs = mocked.call_args.kwargs
    assert kwargs["track"] == "dev"
    assert kwargs["deterministic_only"] is True
    assert kwargs["dry_run"] is True
    assert kwargs["copy_to_desktop"] is False


def test_resumes_generate_error_exit() -> None:
    gen = GenerationResult(status="error", error="Job not found")
    with patch(
        "apps.cli.resume_commands.generate_resume_for_job",
        return_value=gen,
    ):
        with patch("apps.cli.resume_commands.SessionLocal") as sl:
            sl.return_value = MagicMock()
            result = runner.invoke(app, ["resumes", "generate", "missing"])
    assert result.exit_code == 1
    assert "Job not found" in result.stdout
