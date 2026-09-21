from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0005_phase2_audit_hardening"
down_revision = "0004_phase2_market_features"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _require_empty_phase2_tables() -> None:
    connection = op.get_bind()
    snapshot_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM ai_signal_market_snapshot")
    ).scalar_one()
    feature_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM ai_signal_market_feature")
    ).scalar_one()
    if snapshot_count or feature_count:
        raise RuntimeError("PHASE2_REFREEZE_REQUIRED")


def upgrade() -> None:
    _require_empty_phase2_tables()
    with op.batch_alter_table("ai_signal_market_snapshot") as batch:
        batch.add_column(sa.Column("calendar_complete_through", sa.Date, nullable=False))
        batch.add_column(sa.Column("universe_snapshot_id", sa.String(128), nullable=False))
        batch.add_column(sa.Column("source_audits", sa.JSON, nullable=False))
    with op.batch_alter_table("ai_signal_market_feature") as batch:
        batch.add_column(sa.Column("feature_snapshot_hash", sa.String(64), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("ai_signal_market_feature") as batch:
        batch.drop_column("feature_snapshot_hash")
    with op.batch_alter_table("ai_signal_market_snapshot") as batch:
        batch.drop_column("source_audits")
        batch.drop_column("universe_snapshot_id")
        batch.drop_column("calendar_complete_through")
