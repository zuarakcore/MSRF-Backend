"""add programme date place kind and registration_url

Revision ID: e2d3f4a5b6c7
Revises: b0f6a1fec6f3
Create Date: 2026-10-06 00:35:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2d3f4a5b6c7"
down_revision: str | Sequence[str] | None = "b0f6a1fec6f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("programmes", sa.Column("date", sa.Date(), nullable=True))
    op.add_column("programmes", sa.Column("place", sa.String(length=200), nullable=True))
    op.add_column("programmes", sa.Column("kind", sa.String(length=60), nullable=True))
    op.add_column("programmes", sa.Column("registration_url", sa.String(length=500), nullable=True))
    op.alter_column("programmes", "age_group", existing_type=sa.String(length=40), nullable=True)


def downgrade() -> None:
    op.alter_column("programmes", "age_group", existing_type=sa.String(length=40), nullable=False)
    op.drop_column("programmes", "registration_url")
    op.drop_column("programmes", "kind")
    op.drop_column("programmes", "place")
    op.drop_column("programmes", "date")
