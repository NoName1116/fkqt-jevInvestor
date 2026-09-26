from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0009_forward_operations"
down_revision = "0008_forward_corporate_actions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("ai_signal_forward_lease",
                    sa.Column("portfolio_id", sa.String(40), primary_key=True),
                    sa.Column("owner_id", sa.String(64), nullable=False),
                    sa.Column("expires_at", sa.Float, nullable=False))
    op.create_table("ai_signal_forward_stage",
                    sa.Column("id", sa.String(64), primary_key=True),
                    sa.Column("portfolio_id", sa.String(40), nullable=False),
                    sa.Column("trade_date", sa.Date, nullable=False),
                    sa.Column("stage", sa.String(32), nullable=False),
                    sa.Column("config_hash", sa.String(64), nullable=False),
                    sa.Column("status", sa.String(32), nullable=False),
                    sa.Column("attempts", sa.Integer, nullable=False),
                    sa.Column("next_retry_at", sa.Float, nullable=True),
                    sa.Column("input_hash", sa.String(64), nullable=True),
                    sa.Column("error_code", sa.String(128), nullable=True),
                    sa.Column("outcome_json", sa.JSON, nullable=False))
    op.create_table("ai_signal_forward_alert",
                    sa.Column("event_id", sa.String(64), primary_key=True),
                    sa.Column("event_json", sa.JSON, nullable=False),
                    sa.Column("status", sa.String(16), nullable=False),
                    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
                    sa.Column("local_written", sa.Integer, nullable=False, server_default="0"))


def downgrade() -> None:
    for table in ("ai_signal_forward_alert", "ai_signal_forward_stage", "ai_signal_forward_lease"):
        if op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one():
            raise RuntimeError("FORWARD_OPERATION_DOWNGRADE_DATA_LOSS")
    for table in ("ai_signal_forward_alert", "ai_signal_forward_stage", "ai_signal_forward_lease"):
        op.drop_table(table)
