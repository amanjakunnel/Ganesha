"""initial schema

This is an intentionally empty initial migration scaffold created because the
project models have not yet been implemented in SQLAlchemy. Creating an
empty initial revision allows Alembic to be initialized and future migrations
can be generated once models exist.
"""


# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
dependencies = None


def upgrade() -> None:
    # No-op initial migration. Models will be added in subsequent revisions.
    pass


def downgrade() -> None:
    # No-op downgrade for empty initial migration.
    pass
