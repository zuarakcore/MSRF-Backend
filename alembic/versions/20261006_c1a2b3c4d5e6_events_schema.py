"""add events schema

Revision ID: c1a2b3c4d5e6
Revises: b0f6a1fec6f3
Create Date: 2026-10-06 00:25:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "b0f6a1fec6f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=60), server_default="Event", nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("place", sa.String(length=200), nullable=False),
        sa.Column("registration_url", sa.String(length=500), nullable=True),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM("ACTIVE", "INACTIVE", name="record_status", create_type=False),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )


def downgrade() -> None:
    op.drop_table("events")
