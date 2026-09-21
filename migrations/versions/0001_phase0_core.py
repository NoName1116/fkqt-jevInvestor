from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0001_phase0_core"
down_revision = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_signal_provider_call",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_hash", sa.String(64), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_signal_provider_call"),
    )
    op.create_table(
        "ai_signal_schema_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("component", sa.String(64), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_signal_schema_version"),
        sa.UniqueConstraint("component", "version", name="uq_ai_signal_schema_version_component"),
    )


def downgrade() -> None:
    op.drop_table("ai_signal_schema_version")
    op.drop_table("ai_signal_provider_call")
