from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0003_phase1_audit_snapshot"
down_revision = "0002_phase1_portfolio_execution"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_signal_portfolio_snapshot") as batch:
        batch.add_column(sa.Column("details_json", sa.JSON(), nullable=True))
    with op.batch_alter_table("ai_signal_signal_batch") as batch:
        batch.add_column(sa.Column("decision_snapshot_id", sa.String(40), nullable=True))
        batch.create_foreign_key(
            "fk_ai_signal_batch_decision_snapshot",
            "ai_signal_portfolio_snapshot",
            ["decision_snapshot_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_signal_signal_batch") as batch:
        batch.drop_constraint("fk_ai_signal_batch_decision_snapshot", type_="foreignkey")
        batch.drop_column("decision_snapshot_id")
    with op.batch_alter_table("ai_signal_portfolio_snapshot") as batch:
        batch.drop_column("details_json")
