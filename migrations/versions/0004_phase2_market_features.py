from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0004_phase2_market_features"
down_revision = "0003_phase1_audit_snapshot"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_signal_market_snapshot",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("decision_date", sa.Date, nullable=False),
        sa.Column("decision_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_trade_date", sa.Date, nullable=False),
        sa.Column("universe_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("source_manifests", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "decision_date",
            "content_hash",
            name="uq_ai_signal_market_snapshot_date_hash",
        ),
    )
    op.create_table(
        "ai_signal_market_feature",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "market_snapshot_id",
            sa.String(40),
            sa.ForeignKey("ai_signal_market_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("feature_code", sa.String(64), nullable=False),
        sa.Column("feature_version", sa.String(64), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lookback_window", sa.Integer, nullable=False),
        sa.Column("value", sa.Numeric(30, 12), nullable=True),
        sa.Column("missing_reason", sa.String(128), nullable=True),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "market_snapshot_id",
            "symbol",
            "feature_code",
            "feature_version",
            name="uq_ai_signal_market_feature_identity",
        ),
        sa.CheckConstraint(
            "(value IS NULL) != (missing_reason IS NULL)",
            name="feature_value_xor_missing_reason",
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_signal_market_feature")
    op.drop_table("ai_signal_market_snapshot")
