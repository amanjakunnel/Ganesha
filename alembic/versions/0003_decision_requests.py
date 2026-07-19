"""create decision_requests table

Revision ID: 0003_decision_requests
Revises: 0002_create_schema
Create Date: 2026-07-19 19:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

json_type = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)

# revision identifiers, used by Alembic.
revision = '0003_decision_requests'
down_revision = '0002_create_schema'
branch_labels = None
dependencies = None


def upgrade():
    op.create_table(
        'decision_requests',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('entity_type', sa.String(length=128), nullable=False),
        sa.Column('entity_id', sa.String(length=36), nullable=True),
        sa.Column('decision_type', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('reason_code', sa.String(length=128), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('options_json', json_type, nullable=True),
        sa.Column('default_action', sa.String(length=128), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_by', sa.String(length=128), nullable=True),
        sa.Column('selected_action', sa.String(length=128), nullable=True),
        sa.Column('resolution_note', sa.Text(), nullable=True),
        sa.Column('idempotency_key', sa.String(length=255), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
    )
    op.create_index('ix_decision_status', 'decision_requests', ['status'])
    op.create_index('ix_decision_entity', 'decision_requests', ['entity_type', 'entity_id'])
    op.create_index('ix_decision_type', 'decision_requests', ['decision_type'])
    op.create_index('ix_decision_expires', 'decision_requests', ['expires_at'])
    op.create_unique_constraint('uq_decision_idempotency', 'decision_requests', ['idempotency_key'])


def downgrade():
    op.drop_constraint('uq_decision_idempotency', 'decision_requests', type_='unique')
    op.drop_index('ix_decision_expires', table_name='decision_requests')
    op.drop_index('ix_decision_type', table_name='decision_requests')
    op.drop_index('ix_decision_entity', table_name='decision_requests')
    op.drop_index('ix_decision_status', table_name='decision_requests')
    op.drop_table('decision_requests')
