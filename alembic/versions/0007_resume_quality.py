"""resume fact parent context and generation quality status

Revision ID: 0007_resume_quality
Revises: 0006_resume_generation
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_resume_quality"
down_revision = "0006_resume_generation"
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.add_column(
        "resume_facts",
        sa.Column(
            "fact_kind", sa.String(length=32), nullable=False, server_default="bullet"
        ),
    )
    op.add_column(
        "resume_facts",
        sa.Column("parent_entity_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "resume_facts",
        sa.Column("parent_name", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "resume_facts",
        sa.Column("role_title", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "resume_facts",
        sa.Column("gpa_value", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "resume_facts",
        sa.Column("parent_key", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "resume_generations",
        sa.Column(
            "quality_status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
    )
    op.create_index("ix_resume_facts_parent_key", "resume_facts", ["parent_key"])


def downgrade() -> None:
    op.drop_index("ix_resume_facts_parent_key", table_name="resume_facts")
    op.drop_column("resume_generations", "quality_status")
    op.drop_column("resume_facts", "parent_key")
    op.drop_column("resume_facts", "gpa_value")
    op.drop_column("resume_facts", "role_title")
    op.drop_column("resume_facts", "parent_name")
    op.drop_column("resume_facts", "parent_entity_type")
    op.drop_column("resume_facts", "fact_kind")
