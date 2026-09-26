from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0010_forward_safety_evaluation"
down_revision = "0009_forward_operations"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    for table in ("ai_signal_jev_attempt", "ai_signal_llm_decision_attempt"):
        op.add_column(table, sa.Column("diagnostic_json", sa.JSON(none_as_null=True), nullable=True))
        op.add_column(table, sa.Column("usage_json", sa.JSON(none_as_null=True), nullable=True))
    op.create_table("ai_signal_forward_recovery",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("portfolio_id", sa.String(40), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("stage_id", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("expected_stage_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("previous_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM ai_signal_forward_recovery")).scalar_one():
        raise RuntimeError("FORWARD_SAFETY_DOWNGRADE_DATA_LOSS")
    for name in ("ai_signal_jev_attempt", "ai_signal_llm_decision_attempt"):
        table = sa.table(name, sa.column("diagnostic_json", sa.JSON), sa.column("usage_json", sa.JSON))
        if any(value is not None for row in bind.execute(sa.select(table)) for value in row):
            raise RuntimeError("FORWARD_SAFETY_DOWNGRADE_DATA_LOSS")
    op.drop_table("ai_signal_forward_recovery")
    for table in ("ai_signal_llm_decision_attempt", "ai_signal_jev_attempt"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("usage_json")
            batch.drop_column("diagnostic_json")
