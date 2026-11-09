"""application attempts for assisted apply workflow

Revision ID: 0008_application_attempts
Revises: 0007_resume_quality
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_application_attempts"
down_revision = "0007_resume_quality"
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.create_table(
        "application_attempts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_posting_id", sa.String(length=36), nullable=False),
        sa.Column("job_application_id", sa.String(length=36), nullable=True),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("role_title", sa.String(length=512), nullable=False),
        sa.Column("posting_url", sa.String(length=1024), nullable=True),
        sa.Column("application_url", sa.String(length=1024), nullable=False),
        sa.Column("resume_generation_id", sa.String(length=36), nullable=True),
        sa.Column("cover_letter_stamp", sa.String(length=64), nullable=True),
        sa.Column("cover_letter_optional", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("include_cover_letter", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("execution_mode", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("profile_data_ref", sa.JSON(), nullable=True),
        sa.Column("package_json", sa.JSON(), nullable=True),
        sa.Column("unresolved_json", sa.JSON(), nullable=True),
        sa.Column("safe_error", sa.Text(), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_user_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_posting_id"], ["job_postings.id"], name="fk_attempt_job"
        ),
        sa.ForeignKeyConstraint(
            ["job_application_id"],
            ["job_applications.id"],
            name="fk_attempt_job_application",
        ),
        sa.ForeignKeyConstraint(
            ["resume_generation_id"],
            ["resume_generations.id"],
            name="fk_attempt_resume_generation",
        ),
    )
    op.create_index(
        "ix_application_attempts_job", "application_attempts", ["job_posting_id"]
    )
    op.create_index(
        "ix_application_attempts_status", "application_attempts", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_application_attempts_status", table_name="application_attempts")
    op.drop_index("ix_application_attempts_job", table_name="application_attempts")
    op.drop_table("application_attempts")
