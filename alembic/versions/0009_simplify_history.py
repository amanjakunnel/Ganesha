"""simplify tracker history import records

Revision ID: 0009_simplify_history
Revises: 0008_application_attempts
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_simplify_history"
down_revision = "0008_application_attempts"
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.create_table(
        "simplify_history_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=True),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("role_title", sa.String(length=512), nullable=False),
        sa.Column("location", sa.String(length=512), nullable=True),
        sa.Column("job_url", sa.String(length=2048), nullable=True),
        sa.Column("job_url_raw", sa.String(length=2048), nullable=True),
        sa.Column("applied_date_text", sa.String(length=64), nullable=True),
        sa.Column("status_raw", sa.String(length=64), nullable=True),
        sa.Column("status_mapped", sa.String(length=64), nullable=True),
        sa.Column("job_type", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("job_posting_id", sa.String(length=36), nullable=True),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_import_key", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_posting_id"], ["job_postings.id"]),
        sa.UniqueConstraint("source_import_key"),
    )
    op.create_index(
        "ix_simplify_history_records_batch_id",
        "simplify_history_records",
        ["batch_id"],
    )
    op.create_index(
        "ix_simplify_history_records_job_posting_id",
        "simplify_history_records",
        ["job_posting_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_simplify_history_records_job_posting_id",
        table_name="simplify_history_records",
    )
    op.drop_index(
        "ix_simplify_history_records_batch_id", table_name="simplify_history_records"
    )
    op.drop_table("simplify_history_records")
