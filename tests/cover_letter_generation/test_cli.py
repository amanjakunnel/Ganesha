from __future__ import annotations

from typer.testing import CliRunner

from apps.cli.main import app

runner = CliRunner()


def test_doctor_runs_without_secrets() -> None:
    result = runner.invoke(app, ["cover-letters", "doctor"])
    assert result.exit_code == 0
    assert "Cover letter folder:" in result.stdout
    assert "api_key=" in result.stdout
    assert "sk-" not in result.stdout
    assert "ANTHROPIC_API_KEY" not in result.stdout
