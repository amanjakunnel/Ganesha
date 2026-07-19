"""create schema for job intake

Revision ID: 0002_create_schema
Revises: 0001_initial
Create Date: 2026-07-18 20:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

json_type = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)

# revision identifiers, used by Alembic.
revision = '0002_create_schema'
down_revision = '0001_initial'
branch_labels = None
dependencies = None


def upgrade():
    op.create_table(
        'companies',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('canonical_name', sa.String(length=255), nullable=False),
        sa.Column('website_domain', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.UniqueConstraint('canonical_name', name='uq_companies_canonical_name'),
    )

    op.create_table(
        'job_postings',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=True),
        sa.Column('canonical_url', sa.String(length=1024), nullable=True),
        sa.Column('title', sa.String(length=512), nullable=False),
        sa.Column('company_id', sa.String(length=36), nullable=True),
        sa.Column('location', sa.String(length=255), nullable=True),
        sa.Column('workplace_type', sa.String(length=32), nullable=True),
        sa.Column('employment_type', sa.String(length=64), nullable=True),
        sa.Column('description_text', sa.Text(), nullable=False),
        sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('discovered_at', sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column('application_deadline', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('normalized_title', sa.String(length=512), nullable=True),
        sa.Column('description_hash', sa.String(length=128), nullable=False),
        sa.Column('dedupe_key', sa.String(length=128), nullable=False),
        sa.Column('raw_payload', json_type, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_job_company'),
    )
    op.create_index('ix_job_dedupe_key', 'job_postings', ['dedupe_key'])
    op.create_index('ix_job_description_hash', 'job_postings', ['description_hash'])

    op.create_table(
        'job_assessments',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('job_posting_id', sa.String(length=36), nullable=False),
        sa.Column('recommended_track', sa.String(length=32), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('score_explanation', json_type, nullable=True),
        sa.Column('key_skills', json_type, nullable=True),
        sa.Column('missing_or_uncertain_skills', json_type, nullable=True),
        sa.Column('manual_review_reasons', json_type, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.ForeignKeyConstraint(['job_posting_id'], ['job_postings.id'], name='fk_assessment_job'),
    )

    op.create_table(
        'resume_profiles',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('track', sa.String(length=32), nullable=False),
        sa.Column('label', sa.String(length=255), nullable=False),
        sa.Column('source_reference', sa.String(length=1024), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
    )

    op.create_table(
        'referral_tasks',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('job_posting_id', sa.String(length=36), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('cutoff_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.ForeignKeyConstraint(['job_posting_id'], ['job_postings.id'], name='fk_referral_job'),
    )

    op.create_table(
        'audit_events',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('entity_type', sa.String(length=128), nullable=False),
        sa.Column('entity_id', sa.String(length=36), nullable=True),
        sa.Column('event_type', sa.String(length=128), nullable=False),
        sa.Column('payload', json_type, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
    )


def downgrade():
    op.drop_table('audit_events')
    op.drop_table('referral_tasks')
    op.drop_table('resume_profiles')
    op.drop_table('job_assessments')
    op.drop_index('ix_job_description_hash', table_name='job_postings')
    op.drop_index('ix_job_dedupe_key', table_name='job_postings')
    op.drop_table('job_postings')
    op.drop_table('companies')
