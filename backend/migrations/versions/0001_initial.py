"""Initial schema: thread registry and idempotency keys.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "threads",
        sa.Column("thread_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.String(500), nullable=True),
    )
    # A user's threads, newest first (GET /threads) and ownership checks.
    op.create_index("ix_threads_user_updated", "threads", ["user_id", "updated_at"])
    # TTL cleanup scans by age.
    op.create_index("ix_threads_updated_at", "threads", ["updated_at"])

    op.create_table(
        "idempotency_keys",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("path", sa.String(300), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_idempotency_keys_user_id", "idempotency_keys", ["user_id"])
    op.create_index("ix_idempotency_keys_updated_at", "idempotency_keys", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_updated_at", table_name="idempotency_keys")
    op.drop_index("ix_idempotency_keys_user_id", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_threads_updated_at", table_name="threads")
    op.drop_index("ix_threads_user_updated", table_name="threads")
    op.drop_table("threads")
