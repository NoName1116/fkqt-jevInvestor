from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0002_phase1_portfolio_execution"
down_revision = "0001_phase0_core"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    money = sa.Numeric(20, 4)
    cost = sa.Numeric(24, 8)
    ratio = sa.Numeric(12, 8)
    op.create_table(
        "ai_signal_portfolio",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("initial_cash", money, nullable=False),
        sa.Column("cash_balance", money, nullable=False),
        sa.Column("frozen_cash", money, nullable=False),
        sa.Column("realized_pnl", money, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_signal_position",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("portfolio_id", sa.String(40), sa.ForeignKey("ai_signal_portfolio.id"), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("quantity", sa.BigInteger, nullable=False),
        sa.Column("average_cost", cost, nullable=False),
        sa.Column("total_cost", cost, nullable=False),
        sa.Column("last_price", money, nullable=False),
        sa.Column("target_position_pct", ratio, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity >= 0", name="ck_ai_signal_position_quantity_nonnegative"),
        sa.UniqueConstraint("portfolio_id", "symbol", name="uq_ai_signal_position_symbol"),
    )
    op.create_table(
        "ai_signal_position_lot",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("portfolio_id", sa.String(40), sa.ForeignKey("ai_signal_portfolio.id"), nullable=False),
        sa.Column("position_id", sa.String(40), sa.ForeignKey("ai_signal_position.id"), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("acquired_on", sa.Date, nullable=False),
        sa.Column("remaining_quantity", sa.BigInteger, nullable=False),
        sa.Column("unit_cost", cost, nullable=False),
        sa.Column("total_cost", cost, nullable=False),
        sa.CheckConstraint(
            "remaining_quantity >= 0",
            name="ck_ai_signal_position_lot_remaining_quantity_nonnegative",
        ),
    )
    op.create_table(
        "ai_signal_signal_batch",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("portfolio_id", sa.String(40), sa.ForeignKey("ai_signal_portfolio.id"), nullable=False),
        sa.Column("decision_date", sa.Date, nullable=False),
        sa.Column("fixture_version", sa.String(40), nullable=False),
        sa.Column("cash_target_pct", ratio, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "portfolio_id",
            "decision_date",
            "fixture_version",
            name="uq_ai_signal_signal_batch_fixture",
        ),
    )
    op.create_table(
        "ai_signal_signal",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("batch_id", sa.String(40), sa.ForeignKey("ai_signal_signal_batch.id"), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("target_position_pct", ratio, nullable=False),
        sa.Column("confidence", ratio, nullable=False),
        sa.Column("thesis", sa.String(1000), nullable=False),
        sa.Column("invalidation", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("batch_id", "symbol", name="uq_ai_signal_signal_symbol"),
    )
    op.create_table(
        "ai_signal_virtual_order",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("portfolio_id", sa.String(40), sa.ForeignKey("ai_signal_portfolio.id"), nullable=False),
        sa.Column("signal_id", sa.String(40), sa.ForeignKey("ai_signal_signal.id"), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("target_position_pct", ratio, nullable=False),
        sa.Column("planned_execution_date", sa.Date, nullable=False),
        sa.Column("policy_json", sa.JSON, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("status_code", sa.String(64), nullable=True),
        sa.Column("intended_quantity", sa.BigInteger, nullable=False),
        sa.Column("filled_quantity", sa.BigInteger, nullable=False),
        sa.Column("remaining_quantity", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("intended_quantity >= 0", name="ck_ai_signal_virtual_order_intended_quantity_nonnegative"),
        sa.CheckConstraint("filled_quantity >= 0", name="ck_ai_signal_virtual_order_filled_quantity_nonnegative"),
        sa.CheckConstraint("remaining_quantity >= 0", name="ck_ai_signal_virtual_order_remaining_quantity_nonnegative"),
        sa.UniqueConstraint("idempotency_key", name="uq_ai_signal_virtual_order_idempotency"),
    )
    op.create_table(
        "ai_signal_virtual_fill",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("order_id", sa.String(40), sa.ForeignKey("ai_signal_virtual_order.id"), nullable=False),
        sa.Column("fill_sequence", sa.Integer, nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.BigInteger, nullable=False),
        sa.Column("raw_open_price", money, nullable=False),
        sa.Column("fill_price", money, nullable=False),
        sa.Column("gross_value", money, nullable=False),
        sa.Column("commission", money, nullable=False),
        sa.Column("stamp_tax", money, nullable=False),
        sa.Column("total_fees", money, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_ai_signal_virtual_fill_quantity_positive"),
        sa.UniqueConstraint("order_id", "fill_sequence", name="uq_ai_signal_virtual_fill_sequence"),
    )
    op.create_table(
        "ai_signal_portfolio_snapshot",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("portfolio_id", sa.String(40), sa.ForeignKey("ai_signal_portfolio.id"), nullable=False),
        sa.Column("snapshot_type", sa.String(32), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cash_balance", money, nullable=False),
        sa.Column("frozen_cash", money, nullable=False),
        sa.Column("market_value", money, nullable=False),
        sa.Column("total_equity", money, nullable=False),
        sa.Column("realized_pnl", money, nullable=False),
        sa.Column("unrealized_pnl", money, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "portfolio_id",
            "snapshot_type",
            "as_of",
            name="uq_ai_signal_portfolio_snapshot",
        ),
    )
    op.create_table(
        "ai_signal_nav",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("portfolio_id", sa.String(40), sa.ForeignKey("ai_signal_portfolio.id"), nullable=False),
        sa.Column("valuation_date", sa.Date, nullable=False),
        sa.Column("total_equity", money, nullable=False),
        sa.Column("unit_nav", ratio, nullable=False),
        sa.Column("daily_return", ratio, nullable=False),
        sa.Column("cumulative_return", ratio, nullable=False),
        sa.Column("max_drawdown", ratio, nullable=False),
        sa.UniqueConstraint("portfolio_id", "valuation_date", name="uq_ai_signal_nav_date"),
    )


def downgrade() -> None:
    op.drop_table("ai_signal_nav")
    op.drop_table("ai_signal_portfolio_snapshot")
    op.drop_table("ai_signal_virtual_fill")
    op.drop_table("ai_signal_virtual_order")
    op.drop_table("ai_signal_signal")
    op.drop_table("ai_signal_signal_batch")
    op.drop_table("ai_signal_position_lot")
    op.drop_table("ai_signal_position")
    op.drop_table("ai_signal_portfolio")
