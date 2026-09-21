from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from fkqt_jevinvestor.persistence.base import Base


class ProviderCallRecord(Base):
    __tablename__ = "ai_signal_provider_call"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_hash: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    latency_ms: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SchemaVersionRecord(Base):
    __tablename__ = "ai_signal_schema_version"
    __table_args__ = (UniqueConstraint("component", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    component: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


MONEY = Numeric(20, 4)
COST = Numeric(24, 8)
RATIO = Numeric(12, 8)


class PortfolioRecord(Base):
    __tablename__ = "ai_signal_portfolio"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    base_currency: Mapped[str] = mapped_column(String(3))
    initial_cash: Mapped[Decimal] = mapped_column(MONEY)
    cash_balance: Mapped[Decimal] = mapped_column(MONEY)
    frozen_cash: Mapped[Decimal] = mapped_column(MONEY)
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PositionRecord(Base):
    __tablename__ = "ai_signal_position"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "symbol", name="uq_ai_signal_position_symbol"),
        CheckConstraint("quantity >= 0", name="quantity_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("ai_signal_portfolio.id"))
    symbol: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[int] = mapped_column(BigInteger)
    average_cost: Mapped[Decimal] = mapped_column(COST)
    total_cost: Mapped[Decimal] = mapped_column(COST)
    last_price: Mapped[Decimal] = mapped_column(MONEY)
    target_position_pct: Mapped[Decimal] = mapped_column(RATIO)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PositionLotRecord(Base):
    __tablename__ = "ai_signal_position_lot"
    __table_args__ = (
        CheckConstraint("remaining_quantity >= 0", name="remaining_quantity_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("ai_signal_portfolio.id"))
    position_id: Mapped[str] = mapped_column(ForeignKey("ai_signal_position.id"))
    symbol: Mapped[str] = mapped_column(String(16))
    acquired_on: Mapped[date] = mapped_column(Date)
    remaining_quantity: Mapped[int] = mapped_column(BigInteger)
    unit_cost: Mapped[Decimal] = mapped_column(COST)
    total_cost: Mapped[Decimal] = mapped_column(COST)


class SignalBatchRecord(Base):
    __tablename__ = "ai_signal_signal_batch"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "decision_date",
            "fixture_version",
            name="uq_ai_signal_signal_batch_fixture",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("ai_signal_portfolio.id"))
    decision_date: Mapped[date] = mapped_column(Date)
    fixture_version: Mapped[str] = mapped_column(String(40))
    cash_target_pct: Mapped[Decimal] = mapped_column(RATIO)
    status: Mapped[str] = mapped_column(String(16))
    input_hash: Mapped[str] = mapped_column(String(64))
    decision_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_signal_portfolio_snapshot.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SignalRecord(Base):
    __tablename__ = "ai_signal_signal"
    __table_args__ = (
        UniqueConstraint("batch_id", "symbol", name="uq_ai_signal_signal_symbol"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("ai_signal_signal_batch.id"))
    symbol: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(16))
    target_position_pct: Mapped[Decimal] = mapped_column(RATIO)
    confidence: Mapped[Decimal] = mapped_column(RATIO)
    thesis: Mapped[str] = mapped_column(String(1000))
    invalidation: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VirtualOrderRecord(Base):
    __tablename__ = "ai_signal_virtual_order"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ai_signal_virtual_order_idempotency"),
        CheckConstraint("intended_quantity >= 0", name="intended_quantity_nonnegative"),
        CheckConstraint("filled_quantity >= 0", name="filled_quantity_nonnegative"),
        CheckConstraint("remaining_quantity >= 0", name="remaining_quantity_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("ai_signal_portfolio.id"))
    signal_id: Mapped[str] = mapped_column(ForeignKey("ai_signal_signal.id"))
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))
    action: Mapped[str] = mapped_column(String(16))
    target_position_pct: Mapped[Decimal] = mapped_column(RATIO)
    planned_execution_date: Mapped[date] = mapped_column(Date)
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32))
    status_code: Mapped[str | None] = mapped_column(String(64))
    intended_quantity: Mapped[int] = mapped_column(BigInteger)
    filled_quantity: Mapped[int] = mapped_column(BigInteger)
    remaining_quantity: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VirtualFillRecord(Base):
    __tablename__ = "ai_signal_virtual_fill"
    __table_args__ = (
        UniqueConstraint("order_id", "fill_sequence", name="uq_ai_signal_virtual_fill_sequence"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("ai_signal_virtual_order.id"))
    fill_sequence: Mapped[int] = mapped_column(Integer)
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(BigInteger)
    raw_open_price: Mapped[Decimal] = mapped_column(MONEY)
    fill_price: Mapped[Decimal] = mapped_column(MONEY)
    gross_value: Mapped[Decimal] = mapped_column(MONEY)
    commission: Mapped[Decimal] = mapped_column(MONEY)
    stamp_tax: Mapped[Decimal] = mapped_column(MONEY)
    total_fees: Mapped[Decimal] = mapped_column(MONEY)
    trade_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PortfolioSnapshotRecord(Base):
    __tablename__ = "ai_signal_portfolio_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "snapshot_type",
            "as_of",
            name="uq_ai_signal_portfolio_snapshot",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("ai_signal_portfolio.id"))
    snapshot_type: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cash_balance: Mapped[Decimal] = mapped_column(MONEY)
    frozen_cash: Mapped[Decimal] = mapped_column(MONEY)
    market_value: Mapped[Decimal] = mapped_column(MONEY)
    total_equity: Mapped[Decimal] = mapped_column(MONEY)
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY)
    unrealized_pnl: Mapped[Decimal] = mapped_column(MONEY)
    content_hash: Mapped[str] = mapped_column(String(64))
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class NavRecord(Base):
    __tablename__ = "ai_signal_nav"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "valuation_date", name="uq_ai_signal_nav_date"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("ai_signal_portfolio.id"))
    valuation_date: Mapped[date] = mapped_column(Date)
    total_equity: Mapped[Decimal] = mapped_column(MONEY)
    unit_nav: Mapped[Decimal] = mapped_column(RATIO)
    daily_return: Mapped[Decimal] = mapped_column(RATIO)
    cumulative_return: Mapped[Decimal] = mapped_column(RATIO)
    max_drawdown: Mapped[Decimal] = mapped_column(RATIO)


class MarketSnapshotRecord(Base):
    __tablename__ = "ai_signal_market_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "decision_date",
            "content_hash",
            name="uq_ai_signal_market_snapshot_date_hash",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    decision_date: Mapped[date] = mapped_column(Date)
    decision_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_trade_date: Mapped[date] = mapped_column(Date)
    calendar_complete_through: Mapped[date] = mapped_column(Date)
    universe_snapshot_id: Mapped[str] = mapped_column(String(128))
    universe_snapshot_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    storage_path: Mapped[str] = mapped_column(String(1024))
    source_manifests: Mapped[list[str]] = mapped_column(JSON)
    source_audits: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MarketFeatureRecord(Base):
    __tablename__ = "ai_signal_market_feature"
    __table_args__ = (
        UniqueConstraint(
            "market_snapshot_id",
            "symbol",
            "feature_code",
            "feature_version",
            name="uq_ai_signal_market_feature_identity",
        ),
        CheckConstraint(
            "(value IS NULL) != (missing_reason IS NULL)",
            name="feature_value_xor_missing_reason",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    market_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("ai_signal_market_snapshot.id", ondelete="CASCADE")
    )
    symbol: Mapped[str] = mapped_column(String(16))
    feature_code: Mapped[str] = mapped_column(String(64))
    feature_version: Mapped[str] = mapped_column(String(64))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lookback_window: Mapped[int] = mapped_column(Integer)
    value: Mapped[Decimal | None] = mapped_column(Numeric(30, 12), nullable=True)
    missing_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    feature_snapshot_hash: Mapped[str] = mapped_column(String(64))


class JevEvaluationRecord(Base):
    __tablename__ = "ai_signal_jev_evaluation"
    __table_args__ = (
        UniqueConstraint("formal_key", name="uq_ai_signal_jev_evaluation_formal_key"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    formal_key: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decision_date: Mapped[date] = mapped_column(Date)
    decision_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    input_hash: Mapped[str] = mapped_column(String(64))
    provider_name: Mapped[str] = mapped_column(String(64))
    provider_version: Mapped[str] = mapped_column(String(128))
    model_id: Mapped[str] = mapped_column(String(128))
    state_schema_version: Mapped[str] = mapped_column(String(64))
    question_set_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    latest_attempt_sequence: Mapped[int] = mapped_column(Integer)
    current_owner_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JevAttemptRecord(Base):
    __tablename__ = "ai_signal_jev_attempt"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_id",
            "sequence",
            name="uq_ai_signal_jev_attempt_sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("ai_signal_jev_evaluation.id", ondelete="CASCADE")
    )
    run_id: Mapped[str] = mapped_column(String(36))
    owner_token: Mapped[str] = mapped_column(String(36))
    sequence: Mapped[int] = mapped_column(Integer)
    provider_name: Mapped[str] = mapped_column(String(64))
    provider_version: Mapped[str] = mapped_column(String(128))
    model_id: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    raw_response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class JevQuestionResultRecord(Base):
    __tablename__ = "ai_signal_jev_question_result"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_id",
            "question_id",
            name="uq_ai_signal_jev_question_result_question",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("ai_signal_jev_evaluation.id", ondelete="CASCADE")
    )
    question_id: Mapped[str] = mapped_column(String(64))
    question_version: Mapped[str] = mapped_column(String(64))
    criteria_version: Mapped[str] = mapped_column(String(64))
    label_order_json: Mapped[list[str]] = mapped_column(JSON)
    selected_label: Mapped[str] = mapped_column(String(64))
    distribution_json: Mapped[dict[str, str]] = mapped_column(JSON)


class JevRunLinkRecord(Base):
    __tablename__ = "ai_signal_jev_run_link"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "evaluation_id",
            name="uq_ai_signal_jev_run_link_run_evaluation",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36))
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("ai_signal_jev_evaluation.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DecisionEvaluationRecord(Base):
    __tablename__ = "ai_signal_llm_decision_evaluation"
    __table_args__ = (
        UniqueConstraint(
            "formal_key",
            name="uq_ai_signal_llm_decision_evaluation_formal_key",
        ),
        CheckConstraint(
            "raw_response_text IS NULL OR length(raw_response_text) <= 4096",
            name="llm_decision_raw_response_length",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    formal_key: Mapped[str] = mapped_column(String(64))
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("ai_signal_portfolio.id"))
    symbol: Mapped[str] = mapped_column(String(16))
    membership: Mapped[str] = mapped_column(String(16))
    decision_date: Mapped[date] = mapped_column(Date)
    decision_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    input_hash: Mapped[str] = mapped_column(String(64))
    universe_jev_evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("ai_signal_jev_evaluation.id")
    )
    symbol_jev_evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("ai_signal_jev_evaluation.id")
    )
    provider_name: Mapped[str] = mapped_column(String(64))
    provider_version: Mapped[str] = mapped_column(String(128))
    model_id: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    output_schema_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(16))
    thesis: Mapped[str | None] = mapped_column(String(240), nullable=True)
    invalidation: Mapped[str | None] = mapped_column(String(240), nullable=True)
    raw_response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latest_attempt_sequence: Mapped[int] = mapped_column(Integer)
    current_owner_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DecisionAttemptRecord(Base):
    __tablename__ = "ai_signal_llm_decision_attempt"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_id",
            "sequence",
            name="uq_ai_signal_llm_decision_attempt_sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("ai_signal_llm_decision_evaluation.id", ondelete="CASCADE")
    )
    run_id: Mapped[str] = mapped_column(String(36))
    owner_token: Mapped[str] = mapped_column(String(36))
    sequence: Mapped[int] = mapped_column(Integer)
    provider_name: Mapped[str] = mapped_column(String(64))
    provider_version: Mapped[str] = mapped_column(String(128))
    model_id: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    raw_response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class DecisionRunLinkRecord(Base):
    __tablename__ = "ai_signal_llm_decision_run_link"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "evaluation_id",
            name="uq_ai_signal_llm_decision_run_link_run_evaluation",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36))
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("ai_signal_llm_decision_evaluation.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PositionSizingRunRecord(Base):
    __tablename__ = "ai_signal_position_sizing_run"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_ai_signal_position_sizing_run_run"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36))
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("ai_signal_portfolio.id"))
    portfolio_version: Mapped[int] = mapped_column(Integer)
    decision_date: Mapped[date] = mapped_column(Date)
    planned_execution_date: Mapped[date] = mapped_column(Date)
    sizing_version: Mapped[str] = mapped_column(String(64))
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    config_hash: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    decision_portfolio_hash: Mapped[str] = mapped_column(String(64))
    target_batch_hash: Mapped[str] = mapped_column(String(64))
    gross_target_pct: Mapped[Decimal] = mapped_column(RATIO)
    cash_target_pct: Mapped[Decimal] = mapped_column(RATIO)
    run_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signal_batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_signal_signal_batch.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PositionTargetRecord(Base):
    __tablename__ = "ai_signal_position_target"
    __table_args__ = (
        UniqueConstraint(
            "sizing_run_id",
            "symbol",
            name="uq_ai_signal_position_target_symbol",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    sizing_run_id: Mapped[str] = mapped_column(
        ForeignKey("ai_signal_position_sizing_run.id", ondelete="CASCADE")
    )
    decision_evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("ai_signal_llm_decision_evaluation.id")
    )
    symbol: Mapped[str] = mapped_column(String(16))
    requested_action: Mapped[str] = mapped_column(String(16))
    sizing_status: Mapped[str] = mapped_column(String(16))
    current_position_pct: Mapped[Decimal] = mapped_column(RATIO)
    raw_target_position_pct: Mapped[Decimal] = mapped_column(RATIO)
    target_position_pct: Mapped[Decimal] = mapped_column(RATIO)
    signal_action: Mapped[str] = mapped_column(String(16))
    block_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
