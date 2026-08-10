"""Flag rows written by the demo seed.

Seeded usage history is indistinguishable from real model spend once it is in the table,
which quietly undermines the cost dashboard. This lets the analytics endpoints exclude it.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TABLES = ("llm_calls", "usage_records")


def _has(table: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == "is_demo_data" for c in inspector.get_columns(table))


def upgrade() -> None:
    # The 0001 baseline is built from live model metadata, so on a fresh database the
    # column already exists. Same idempotency reasoning as 0002.
    for table in TABLES:
        if _has(table):
            continue
        op.add_column(
            table,
            sa.Column("is_demo_data", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )
        op.create_index(f"ix_{table}_is_demo_data", table, ["is_demo_data"])


def downgrade() -> None:
    for table in TABLES:
        if _has(table):
            op.drop_index(f"ix_{table}_is_demo_data", table_name=table)
            op.drop_column(table, "is_demo_data")
