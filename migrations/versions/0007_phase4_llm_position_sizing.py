from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0007_phase4_llm_position_sizing"
down_revision = "0006_phase3_jev_pnl_probabilities"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_signal_llm_decision_evaluation",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("formal_key", sa.String(64), nullable=False),
        sa.Column(
            "portfolio_id",
            sa.String(40),
            sa.ForeignKey("ai_signal_portfolio.id"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("membership", sa.String(16), nullable=False),
        sa.Column("decision_date", sa.Date, nullable=False),
        sa.Column("decision_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_json", sa.JSON, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column(
            "universe_jev_evaluation_id",
            sa.String(40),
            sa.ForeignKey("ai_signal_jev_evaluation.id"),
            nullable=False,
        ),
        sa.Column(
            "symbol_jev_evaluation_id",
            sa.String(40),
            sa.ForeignKey("ai_signal_jev_evaluation.id"),
            nullable=False,
        ),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column("provider_version", sa.String(128), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("provider_base_url", sa.String(512), nullable=False),
        sa.Column("reasoning_effort", sa.String(16), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("output_schema_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("thesis", sa.String(240), nullable=True),
        sa.Column("invalidation", sa.String(240), nullable=True),
        sa.Column("raw_response_text", sa.Text, nullable=True),
        sa.Column("raw_response_hash", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("latest_attempt_sequence", sa.Integer, nullable=False),
        sa.Column("current_owner_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "raw_response_text IS NULL OR length(raw_response_text) <= 4096",
            name="llm_decision_raw_response_length",
        ),
        sa.UniqueConstraint(
            "formal_key",
            name="uq_ai_signal_llm_decision_evaluation_formal_key",
        ),
    )
    op.create_table(
        "ai_signal_llm_decision_attempt",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "evaluation_id",
            sa.String(40),
            sa.ForeignKey("ai_signal_llm_decision_evaluation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("owner_token", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column("provider_version", sa.String(128), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("raw_response_hash", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.UniqueConstraint(
            "evaluation_id",
            "sequence",
            name="uq_ai_signal_llm_decision_attempt_sequence",
        ),
    )
    op.create_table(
        "ai_signal_llm_decision_run_link",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column(
            "evaluation_id",
            sa.String(40),
            sa.ForeignKey("ai_signal_llm_decision_evaluation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "run_id",
            "evaluation_id",
            name="uq_ai_signal_llm_decision_run_link_run_evaluation",
        ),
    )
    op.create_table(
        "ai_signal_position_sizing_run",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column(
            "portfolio_id",
            sa.String(40),
            sa.ForeignKey("ai_signal_portfolio.id"),
            nullable=False,
        ),
        sa.Column("portfolio_version", sa.Integer, nullable=False),
        sa.Column("decision_date", sa.Date, nullable=False),
        sa.Column("planned_execution_date", sa.Date, nullable=False),
        sa.Column("sizing_version", sa.String(64), nullable=False),
        sa.Column("config_json", sa.JSON, nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("decision_portfolio_hash", sa.String(64), nullable=False),
        sa.Column("target_batch_hash", sa.String(64), nullable=False),
        sa.Column("gross_target_pct", sa.Numeric(12, 8), nullable=False),
        sa.Column("cash_target_pct", sa.Numeric(12, 8), nullable=False),
        sa.Column("run_code", sa.String(128), nullable=True),
        sa.Column(
            "signal_batch_id",
            sa.String(40),
            sa.ForeignKey("ai_signal_signal_batch.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_ai_signal_position_sizing_run_run"),
    )
    op.create_table(
        "ai_signal_position_target",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "sizing_run_id",
            sa.String(40),
            sa.ForeignKey("ai_signal_position_sizing_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "decision_evaluation_id",
            sa.String(40),
            sa.ForeignKey("ai_signal_llm_decision_evaluation.id"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("requested_action", sa.String(16), nullable=False),
        sa.Column("sizing_status", sa.String(16), nullable=False),
        sa.Column("current_position_pct", sa.Numeric(12, 8), nullable=False),
        sa.Column("raw_target_position_pct", sa.Numeric(12, 8), nullable=False),
        sa.Column("target_position_pct", sa.Numeric(12, 8), nullable=False),
        sa.Column("signal_action", sa.String(16), nullable=False),
        sa.Column("block_code", sa.String(128), nullable=True),
        sa.UniqueConstraint(
            "sizing_run_id",
            "symbol",
            name="uq_ai_signal_position_target_symbol",
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_signal_position_target")
    op.drop_table("ai_signal_position_sizing_run")
    op.drop_table("ai_signal_llm_decision_run_link")
    op.drop_table("ai_signal_llm_decision_attempt")
    op.drop_table("ai_signal_llm_decision_evaluation")
