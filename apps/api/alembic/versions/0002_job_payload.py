"""Store task kwargs on the job ledger.

Needed by the database-backed worker (single-node mode, no Redis), which has to replay a
task's arguments from the row rather than from a queue message. Also makes the jobs page
show what a task was actually asked to do.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(JSONB, "postgresql")


def _has_payload() -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(column["name"] == "payload" for column in inspector.get_columns("jobs"))


def upgrade() -> None:
    # The 0001 baseline is materialised from the live model metadata, so on a *fresh*
    # database this column already exists and adding it again would fail. Skipping when
    # present keeps both paths working: fresh installs and databases created before the
    # model gained the column.
    if _has_payload():
        return
    op.add_column(
        "jobs",
        sa.Column("payload", JSON_TYPE, nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    if _has_payload():
        op.drop_column("jobs", "payload")
