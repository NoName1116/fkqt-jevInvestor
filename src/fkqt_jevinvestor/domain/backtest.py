from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot
from fkqt_jevinvestor.domain.market_features import MarketFeatureSnapshot
from fkqt_jevinvestor.domain.portfolio import PortfolioState


class ExperimentArm(StrEnum):
    A_LLM = "A_LLM"
    B_JEV_LLM = "B_JEV_LLM"
    C_JEV_DIRECT = "C_JEV_DIRECT"
    D_RULE = "D_RULE"


class DecisionAction(StrEnum):
    ENTER = "ENTER"
    KEEP = "KEEP"
    EXIT = "EXIT"
    AVOID = "AVOID"
    NO_SIGNAL = "NO_SIGNAL"


class TargetPosition(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    action: DecisionAction
    target_position_pct: Decimal = Field(ge=0, le=1)
    sizing_version: str
    sizing_input_hash: str = Field(min_length=64, max_length=64)


class TargetPositionBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_date: date
    planned_execution_date: date
    experiment_arm: ExperimentArm
    targets: tuple[TargetPosition, ...]
    input_hash: str = Field(min_length=64, max_length=64)


class DecisionReplayDay(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_date: date
    decision_cutoff: datetime
    planned_execution_date: date
    universe_snapshot_hash: str = Field(min_length=64, max_length=64)
    market_snapshot_hash: str = Field(min_length=64, max_length=64)
    feature_snapshot_hash: str = Field(min_length=64, max_length=64)
    features: Mapping[str, MarketFeatureSnapshot]


class ReplayDay(DecisionReplayDay):
    execution_market: Mapping[str, MarketExecutionSnapshot]

    def decision_view(self) -> DecisionReplayDay:
        return DecisionReplayDay.model_validate(
            self.model_dump(exclude={"execution_market"})
        )


class BacktestConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    experiment_arm: ExperimentArm
    start_date: date
    end_date: date
    warmup_trading_days: int = Field(ge=0)
    initial_cash: Decimal = Field(gt=0)
    benchmark_symbol: str
    dataset_id: str
    dataset_hash: str = Field(min_length=64, max_length=64)
    execution_policy_version: str
    sizing_version: str


class DailyBacktestRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_date: date
    execution_date: date
    total_equity: Decimal
    cash_balance: Decimal
    market_value: Decimal
    daily_return: Decimal
    cumulative_return: Decimal
    drawdown: Decimal
    turnover: Decimal
    fees: Decimal
    submitted_orders: int = Field(ge=0)
    filled_orders: int = Field(ge=0)
    rejected_orders: int = Field(ge=0)


class BacktestExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_date: date
    execution_date: date
    portfolio_after: PortfolioState
    total_equity: Decimal = Field(gt=0)
    cash_balance: Decimal = Field(ge=0)
    market_value: Decimal = Field(ge=0)
    gross_traded_value: Decimal = Field(ge=0)
    total_fees: Decimal = Field(ge=0)
    submitted_orders: int = Field(ge=0)
    filled_orders: int = Field(ge=0)
    rejected_orders: int = Field(ge=0)


class BacktestSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    experiment_arm: ExperimentArm
    trading_days: int = Field(ge=0)
    cumulative_return: Decimal
    annualized_return: Decimal
    max_drawdown: Decimal
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    turnover: Decimal
    total_fees: Decimal
    submitted_orders: int = Field(ge=0)
    filled_orders: int = Field(ge=0)
    rejected_orders: int = Field(ge=0)
    fill_rate: Decimal | None
    config_hash: str = Field(min_length=64, max_length=64)
    result_hash: str = Field(min_length=64, max_length=64)


class BacktestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    config: BacktestConfig
    daily_records: tuple[DailyBacktestRecord, ...]
    summary: BacktestSummary


class ReplayDataProvider(Protocol):
    async def decision_dates(self, start: date, end: date) -> tuple[date, ...]: ...

    async def load_day(self, decision_date: date) -> ReplayDay: ...


class TargetProvider(Protocol):
    async def build_targets(
        self,
        config: BacktestConfig,
        day: DecisionReplayDay,
        portfolio: PortfolioState,
    ) -> TargetPositionBatch: ...


class ExecutionPort(Protocol):
    async def execute(
        self,
        portfolio: PortfolioState,
        targets: TargetPositionBatch,
        market: Mapping[str, MarketExecutionSnapshot],
    ) -> BacktestExecutionResult: ...
