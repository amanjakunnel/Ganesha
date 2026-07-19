"""Integration test for Alembic migrations."""
from __future__ import annotations

import tempfile
from pathlib import Path

from alembic.config import Config
from alembic import command
from sqlalchemy import inspect



def test_migration_creates_all_tables() -> None:
    """Test that running migrations creates all required tables."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db_url = f"sqlite:///{db_path}"
        
        # Create Alembic config for test database
        alembic_dir = Path(__file__).parent.parent.parent / "alembic"
        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(alembic_dir))
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)
        
        # Run migrations
        command.upgrade(alembic_cfg, "head")
        
        # Verify all expected tables exist
        from sqlalchemy import create_engine

        engine = create_engine(db_url)
        
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        
        expected_tables = [
            "companies",
            "job_postings",
            "job_assessments",
            "resume_profiles",
            "referral_tasks",
            "audit_events",
            "alembic_version",
        ]
        
        for table in expected_tables:
            assert table in tables, f"Expected table {table} not found in {tables}"
        
        # Verify key columns exist in job_postings
        job_postings_columns = [col["name"] for col in inspector.get_columns("job_postings")]
        expected_job_columns = [
            "id",
            "source",
            "external_id",
            "canonical_url",
            "title",
            "company_id",
            "location",
            "workplace_type",
            "employment_type",
            "description_text",
            "posted_at",
            "discovered_at",
            "application_deadline",
            "status",
            "normalized_title",
            "description_hash",
            "dedupe_key",
            "raw_payload",
            "created_at",
            "updated_at",
        ]
        
        for col in expected_job_columns:
            assert col in job_postings_columns, f"Expected column {col} not found in job_postings"
        
        # Verify indexes exist
        indexes = inspector.get_indexes("job_postings")
        index_names = [idx["name"] for idx in indexes]
        assert "ix_job_dedupe_key" in index_names
        assert "ix_job_description_hash" in index_names
        
        # Verify foreign key exists
        foreign_keys = inspector.get_foreign_keys("job_postings")
        assert len(foreign_keys) > 0
        assert any(fk["constrained_columns"] == ["company_id"] for fk in foreign_keys)


def test_upgrade_downgrade_recreates_tables() -> None:
    """Test upgrading to head, downgrading to 0002, and upgrading again recreates decision_requests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db_url = f"sqlite:///{db_path}"

        alembic_dir = Path(__file__).parent.parent.parent / "alembic"
        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(alembic_dir))
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)

        # Upgrade to head
        command.upgrade(alembic_cfg, "head")
        from sqlalchemy import create_engine

        engine = create_engine(db_url)
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert "decision_requests" in tables

        # Downgrade to 0002_create_schema and ensure table removed
        command.downgrade(alembic_cfg, "0002_create_schema")
        inspector = inspect(engine)
        tables_after = inspector.get_table_names()
        assert "decision_requests" not in tables_after

        # Upgrade again to head and ensure table exists
        command.upgrade(alembic_cfg, "head")
        inspector = inspect(engine)
        tables_after_upgrade = inspector.get_table_names()
        assert "decision_requests" in tables_after_upgrade

        engine.dispose()


def test_orm_models_match_migration() -> None:
    """Test that ORM models can be used with migrated database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db_url = f"sqlite:///{db_path}"
        
        # Create Alembic config for test database
        alembic_dir = Path(__file__).parent.parent.parent / "alembic"
        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(alembic_dir))
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)
        
        # Run migrations
        command.upgrade(alembic_cfg, "head")
        
        # Try to use ORM models
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        
        engine = create_engine(db_url)
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()
        
        # Create a company using ORM
        from packages.core.domain.models import Company
        
        company = Company(canonical_name="Test Company")
        session.add(company)
        session.commit()
        
        # Verify it was created
        retrieved = session.query(Company).filter(Company.canonical_name == "Test Company").first()
        assert retrieved is not None
        assert retrieved.canonical_name == "Test Company"

        engine.dispose()
        
        session.close()
