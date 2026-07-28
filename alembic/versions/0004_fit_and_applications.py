"""job fit results and applications

Revision ID: 0004_fit_and_applications
Revises: 0003_decision_requests
Create Date: 2026-07-28 12:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

json_type = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)

revision = "0004_fit_and_applications"
down_revision = "0003_decision_requests"
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.create_table(
        "job_fit_results",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_posting_id", sa.String(length=36), nullable=False),
        sa.Column("recommended_track", sa.String(length=32), nullable=False),
        sa.Column("key_requirements", json_type, nullable=True),
        sa.Column("missing_evidence", json_type, nullable=True),
        sa.Column("readiness_status", sa.String(length=32), nullable=False),
        sa.Column("next_action", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_posting_id"], ["job_postings.id"], name="fk_fit_job"
        ),
        sa.UniqueConstraint("job_posting_id", name="uq_fit_job_posting"),
    )

    op.create_table(
        "job_applications",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_posting_id", sa.String(length=36), nullable=False),
        sa.Column("selected_track", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_posting_id"], ["job_postings.id"], name="fk_application_job"
        ),
        sa.UniqueConstraint("job_posting_id", name="uq_application_job_posting"),
    )


def downgrade() -> None:
    op.drop_table("job_applications")
    op.drop_table("job_fit_results")
