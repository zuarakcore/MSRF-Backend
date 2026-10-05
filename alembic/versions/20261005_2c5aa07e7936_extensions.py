"""extensions

Revision ID: 2c5aa07e7936
Revises:
Create Date: 2026-10-05 20:09:45.275185

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2c5aa07e7936"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """citext: case-insensitive emails. pg_trgm: fast ILIKE '%term%' search."""
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    """Extensions are left in place: other objects may depend on them."""
