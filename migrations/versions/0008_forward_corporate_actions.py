from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0008_forward_corporate_actions"
down_revision = "0007_phase4_llm_position_sizing"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    for table in ("ai_signal_portfolio", "ai_signal_portfolio_snapshot"):
        op.add_column(table, sa.Column("dividend_receivable", sa.Numeric(20, 4), nullable=False, server_default="0"))
        op.add_column(table, sa.Column("dividend_income", sa.Numeric(20, 4), nullable=False, server_default="0"))
    op.add_column("ai_signal_position_lot", sa.Column("available_on", sa.Date, nullable=True))
    op.create_table(
        "ai_signal_corporate_action_event",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("source_version", sa.String(128), primary_key=True),
        sa.Column("event_json", sa.JSON, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "ai_signal_corporate_entitlement",
        sa.Column("portfolio_id", sa.String(40), sa.ForeignKey("ai_signal_portfolio.id"), primary_key=True),
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("source_version", sa.String(128), nullable=False),
        sa.Column("entitlement_json", sa.JSON, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "ai_signal_corporate_stage",
        sa.Column("portfolio_id", sa.String(40), sa.ForeignKey("ai_signal_portfolio.id"), primary_key=True),
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("stage", sa.String(8), primary_key=True),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("processed_date", sa.Date, nullable=False),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM ai_signal_corporate_entitlement")).scalar_one():
        raise RuntimeError("CORPORATE_ACTION_DOWNGRADE_DATA_LOSS")
    for table in ("ai_signal_portfolio", "ai_signal_portfolio_snapshot"):
        if bind.execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE dividend_receivable <> 0 OR dividend_income <> 0")).scalar_one():
            raise RuntimeError("CORPORATE_ACTION_DOWNGRADE_DATA_LOSS")
    if bind.execute(sa.text("SELECT COUNT(*) FROM ai_signal_position_lot WHERE available_on IS NOT NULL")).scalar_one():
        raise RuntimeError("CORPORATE_ACTION_DOWNGRADE_DATA_LOSS")
    for table in ("ai_signal_corporate_stage", "ai_signal_corporate_entitlement", "ai_signal_corporate_action_event"):
        op.drop_table(table)
    op.drop_column("ai_signal_position_lot", "available_on")
    for table in ("ai_signal_portfolio_snapshot", "ai_signal_portfolio"):
        op.drop_column(table, "dividend_income")
        op.drop_column(table, "dividend_receivable")
