"""resume corpus, facts, and generation audit tables

Revision ID: 0006_resume_generation
Revises: 0005_job_search_sources
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

json_type = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)

revision = "0006_resume_generation"
down_revision = "0005_job_search_sources"
branch_labels = None
dependencies = None


def upgrade() -> None:
    op.create_table(
        "resume_source_documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("absolute_path", sa.String(length=2048), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("track", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("parse_status", sa.String(length=32), nullable=False),
        sa.Column("fact_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_scanned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("absolute_path"),
    )
    op.create_table(
        "resume_facts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("section", sa.String(length=64), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("employer_or_project", sa.String(length=512), nullable=True),
        sa.Column("skills_json", json_type, nullable=True),
        sa.Column("dates_text", sa.String(length=255), nullable=True),
        sa.Column("metrics_json", json_type, nullable=True),
        sa.Column("verification_state", sa.String(length=32), nullable=False),
        sa.Column("conflict_group", sa.String(length=128), nullable=True),
        sa.Column("usable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["resume_source_documents.id"],
            name="fk_resume_facts_source",
        ),
    )
    op.create_index("ix_resume_facts_hash", "resume_facts", ["content_hash"])
    op.create_index("ix_resume_facts_source", "resume_facts", ["source_document_id"])
    op.create_table(
        "resume_generations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_posting_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("selected_track", sa.String(length=64), nullable=True),
        sa.Column("docx_path", sa.String(length=2048), nullable=True),
        sa.Column("pdf_path", sa.String(length=2048), nullable=True),
        sa.Column("desktop_pdf_path", sa.String(length=2048), nullable=True),
        sa.Column("source_files_json", json_type, nullable=True),
        sa.Column("fact_ids_json", json_type, nullable=True),
        sa.Column("job_description_hash", sa.String(length=128), nullable=True),
        sa.Column("template_version", sa.String(length=64), nullable=True),
        sa.Column("semantic_provider", sa.String(length=64), nullable=True),
        sa.Column("semantic_model", sa.String(length=128), nullable=True),
        sa.Column("validation_json", json_type, nullable=True),
        sa.Column("warnings_json", json_type, nullable=True),
        sa.Column("gaps_json", json_type, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_posting_id"],
            ["job_postings.id"],
            name="fk_resume_gen_job",
        ),
    )


def downgrade() -> None:
    op.drop_table("resume_generations")
    op.drop_index("ix_resume_facts_source", table_name="resume_facts")
    op.drop_index("ix_resume_facts_hash", table_name="resume_facts")
    op.drop_table("resume_facts")
    op.drop_table("resume_source_documents")
