"""CLI tests using typer.testing.CliRunner."""

from __future__ import annotations


from typer.testing import CliRunner

from apps.cli.main import app

runner = CliRunner()


def test_root_help_contains_jobs() -> None:
    """Test that root help lists the jobs command."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "jobs" in result.stdout


def test_jobs_help_lists_subcommands() -> None:
    """Test that jobs --help lists all implemented subcommands."""
    result = runner.invoke(app, ["jobs", "--help"])
    assert result.exit_code == 0
    # Check for required subcommands
    assert "import-csv" in result.stdout
    assert "import-json" in result.stdout
    assert "add" in result.stdout
    assert "list" in result.stdout
    assert "show" in result.stdout
    assert "assess" in result.stdout
    assert "triage" in result.stdout
    assert "approve" in result.stdout
    assert "skip" in result.stdout
    assert "defer" in result.stdout
    assert "referral-start" in result.stdout
    assert "referral-status" in result.stdout


'''def test_import_csv_with_fixture() -> None:
    """Test CSV import using the tracked fixture with temporary SQLite database."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "jobs_sample.csv"
    
    # Use a temporary SQLite database for this test
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        # Set environment variable to use SQLite
        original_db_url = os.environ.get("DATABASE_URL")
        try:
            os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
            result = runner.invoke(app, ["jobs", "import-csv", str(fixture_path)])
            # Command should succeed with test database
            assert result.exit_code == 0
            assert "Imported:" in result.stdout
        finally:
            if original_db_url is not None:
                os.environ["DATABASE_URL"] = original_db_url
            elif "DATABASE_URL" in os.environ:
                del os.environ["DATABASE_URL"]'''


def test_jobs_help_lists_import_csv() -> None:
    result = runner.invoke(app, ["jobs", "--help"])

    assert result.exit_code == 0, result.output
    assert "import-csv" in result.output


def test_destructive_command_requires_confirm() -> None:
    """Test that destructive commands require --confirm flag."""
    # Test approve without confirm - should exit with code 2
    result = runner.invoke(app, ["jobs", "approve", "some-id"])
    assert result.exit_code == 2

    # Test skip without confirm - should exit with code 2
    result = runner.invoke(app, ["jobs", "skip", "some-id"])
    assert result.exit_code == 2
