from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0006_phase3_jev_pnl_probabilities"
down_revision = "0005_phase2_audit_hardening"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_signal_jev_evaluation",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("formal_key", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=True),
        sa.Column("decision_date", sa.Date, nullable=False),
        sa.Column("decision_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state_json", sa.JSON, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column("provider_version", sa.String(128), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("state_schema_version", sa.String(64), nullable=False),
        sa.Column("question_set_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("latest_attempt_sequence", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "formal_key",
            name="uq_ai_signal_jev_evaluation_formal_key",
        ),
    )
    op.create_table(
        "ai_signal_jev_attempt",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "evaluation_id",
            sa.String(40),
            sa.ForeignKey("ai_signal_jev_evaluation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column("provider_version", sa.String(128), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("raw_response_hash", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.UniqueConstraint(
            "evaluation_id",
            "sequence",
            name="uq_ai_signal_jev_attempt_sequence",
        ),
    )
    op.create_table(
        "ai_signal_jev_question_result",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "evaluation_id",
            sa.String(40),
            sa.ForeignKey("ai_signal_jev_evaluation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_id", sa.String(64), nullable=False),
        sa.Column("question_version", sa.String(64), nullable=False),
        sa.Column("criteria_version", sa.String(64), nullable=False),
        sa.Column("label_order_json", sa.JSON, nullable=False),
        sa.Column("selected_label", sa.String(64), nullable=False),
        sa.Column("distribution_json", sa.JSON, nullable=False),
        sa.UniqueConstraint(
            "evaluation_id",
            "question_id",
            name="uq_ai_signal_jev_question_result_question",
        ),
    )
    op.create_table(
        "ai_signal_jev_run_link",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column(
            "evaluation_id",
            sa.String(40),
            sa.ForeignKey("ai_signal_jev_evaluation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "run_id",
            "evaluation_id",
            name="uq_ai_signal_jev_run_link_run_evaluation",
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_signal_jev_run_link")
    op.drop_table("ai_signal_jev_question_result")
    op.drop_table("ai_signal_jev_attempt")
    op.drop_table("ai_signal_jev_evaluation")

