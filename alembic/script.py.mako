"""
Template for generating migration scripts.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '${up_revision}'
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade():
    """Auto-generated upgrade stub"""
    pass


def downgrade():
    """Auto-generated downgrade stub"""
    pass
