"""job search source metadata and referral contacts

Revision ID: 0005_job_search_sources
Revises: 0004_fit_and_applications
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

json_type = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)

revision = "0005_job_search_sources"
down_revision = "0004_fit_and_applications"
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("normalized_key", sa.String(length=255), nullable=True))
    op.create_index("ix_companies_normalized_key", "companies", ["normalized_key"], unique=False)

    op.add_column("job_postings", sa.Column("source_name", sa.String(length=64), nullable=True))
    op.add_column("job_postings", sa.Column("source_type", sa.String(length=64), nullable=True))
    op.add_column("job_postings", sa.Column("company_apply_url", sa.String(length=1024), nullable=True))
    op.add_column(
        "job_postings",
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_postings",
        sa.Column("source_import_key", sa.String(length=255), nullable=True),
    )
    op.add_column("job_postings", sa.Column("intake_metadata", json_type, nullable=True))
    op.create_index(
        "ix_job_source_import_key",
        "job_postings",
        ["source_import_key"],
        unique=True,
    )
    op.create_index("ix_job_company_apply_url", "job_postings", ["company_apply_url"], unique=False)

    op.create_table(
        "referral_contacts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("company_id", sa.String(length=36), nullable=True),
        sa.Column("company_name_raw", sa.String(length=255), nullable=False),
        sa.Column("contact_name", sa.String(length=255), nullable=False),
        sa.Column("position", sa.String(length=512), nullable=True),
        sa.Column("team", sa.String(length=255), nullable=True),
        sa.Column("locations", sa.String(length=512), nullable=True),
        sa.Column("alternate_location", sa.String(length=255), nullable=True),
        sa.Column("source_import_key", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_referral_contact_company"),
        sa.UniqueConstraint("source_import_key", name="uq_referral_contact_import_key"),
    )
    op.create_index("ix_referral_contacts_company_id", "referral_contacts", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_referral_contacts_company_id", table_name="referral_contacts")
    op.drop_table("referral_contacts")
    op.drop_index("ix_job_company_apply_url", table_name="job_postings")
    op.drop_index("ix_job_source_import_key", table_name="job_postings")
    op.drop_column("job_postings", "intake_metadata")
    op.drop_column("job_postings", "source_import_key")
    op.drop_column("job_postings", "scraped_at")
    op.drop_column("job_postings", "company_apply_url")
    op.drop_column("job_postings", "source_type")
    op.drop_column("job_postings", "source_name")
    op.drop_index("ix_companies_normalized_key", table_name="companies")
    op.drop_column("companies", "normalized_key")
