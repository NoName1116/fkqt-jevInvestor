"""回测测试使用的内存假件。

全部在内存中构造，不访问网络、磁盘数据库或任何外部服务；不使用当前时间或
随机数，相同输入两次运行结果完全一致。所有金额与比例均用 ``Decimal`` 显式
字符串构造。
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from fkqt_jevinvestor.domain.backtest import (
    BacktestConfig,
    BacktestExecutionResult,
    DecisionAction,
    DecisionReplayDay,
    ExperimentArm,
    ReplayDay,
    TargetPosition,
    TargetPositionBatch,
)
from fkqt_jevinvestor.domain.market import (
    MarketExecutionSnapshot,
    TradingDayStatus,
    TradingStatus,
)
from fkqt_jevinvestor.domain.market_features import (
    AdjustmentMode,
    DailyBar,
    FeatureValue,
    MarketFeatureSnapshot,
    SecurityTradeState,
)
from fkqt_jevinvestor.domain.portfolio import PortfolioState, PositionState

DEFAULT_SYMBOL: str = "600000.SH"
DEFAULT_HASH: str = "0" * 64

_DEFAULT_DECISION_DATE: date = date(2026, 1, 5)
_DEFAULT_EXECUTION_DATE: date = date(2026, 1, 6)
_DEFAULT_CUTOFF: datetime = datetime(2026, 1, 5, 15, 0, 0, tzinfo=UTC)
_DEFAULT_FEATURE_AS_OF: datetime = datetime(2026, 1, 5, 9, 30, 0, tzinfo=UTC)


def make_config(
    *,
    run_id: str = "run-default",
    experiment_arm: ExperimentArm = ExperimentArm.A_LLM,
    start_date: date = _DEFAULT_DECISION_DATE,
    end_date: date = _DEFAULT_DECISION_DATE,
    warmup_trading_days: int = 0,
    initial_cash: Decimal = Decimal(1000000),
    benchmark_symbol: str = "000300.SH",
    dataset_id: str = "dataset-default",
    dataset_hash: str = DEFAULT_HASH,
    execution_policy_version: str = "exec-policy-v1",
    sizing_version: str = "sizing-v1",
) -> BacktestConfig:
    """一行构造出 BacktestConfig，四组共用同一套起点，只改需要改的字段。"""
    return BacktestConfig(
        run_id=run_id,
        experiment_arm=experiment_arm,
        start_date=start_date,
        end_date=end_date,
        warmup_trading_days=warmup_trading_days,
        initial_cash=initial_cash,
        benchmark_symbol=benchmark_symbol,
        dataset_id=dataset_id,
        dataset_hash=dataset_hash,
        execution_policy_version=execution_policy_version,
        sizing_version=sizing_version,
    )


def make_daily_bar(
    *,
    symbol: str = DEFAULT_SYMBOL,
    trade_date: date = _DEFAULT_DECISION_DATE,
    open: Decimal = Decimal("10.00"),
    high: Decimal = Decimal("10.50"),
    low: Decimal = Decimal("9.80"),
    close: Decimal = Decimal("10.20"),
    previous_close: Decimal = Decimal("10.00"),
    volume: Decimal = Decimal(1000000),
    amount_cny: Decimal = Decimal(10200000),
    adjustment_mode: AdjustmentMode = AdjustmentMode.NONE,
) -> DailyBar:
    """构造一根日线。"""
    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        open=open,
        high=high,
        low=low,
        close=close,
        previous_close=previous_close,
        volume=volume,
        amount_cny=amount_cny,
        adjustment_mode=adjustment_mode,
    )


def make_security_state(
    *,
    symbol: str = DEFAULT_SYMBOL,
    trade_date: date = _DEFAULT_DECISION_DATE,
    trading_day_status: str = "OPEN",
    trading_status: str = "TRADING",
    is_st_or_delisting_risk: bool | None = False,
    upper_limit_price: Decimal | None = Decimal("11.00"),
    lower_limit_price: Decimal | None = Decimal("9.00"),
    is_initial_no_limit_period: bool | None = False,
    corporate_action_status: str = "NONE",
    market: str = "SH",
    board: str = "MAIN",
    listing_date: date | None = date(2000, 1, 1),
    missing_reasons: tuple[str, ...] = (),
) -> SecurityTradeState:
    """构造一份证券状态。"""
    return SecurityTradeState(
        symbol=symbol,
        trade_date=trade_date,
        trading_day_status=trading_day_status,
        trading_status=trading_status,
        is_st_or_delisting_risk=is_st_or_delisting_risk,
        upper_limit_price=upper_limit_price,
        lower_limit_price=lower_limit_price,
        is_initial_no_limit_period=is_initial_no_limit_period,
        corporate_action_status=corporate_action_status,
        market=market,
        board=board,
        listing_date=listing_date,
        missing_reasons=missing_reasons,
    )


def make_feature_value(
    *,
    feature_code: str = "momentum_20",
    feature_version: str = "v1",
    as_of: datetime = _DEFAULT_FEATURE_AS_OF,
    lookback_window: int = 20,
    value: Decimal | None = Decimal("0.05"),
    missing_reason: str | None = None,
    source_snapshot_hash: str = DEFAULT_HASH,
) -> FeatureValue:
    """构造一个特征值；传 ``value=None`` 并给 ``missing_reason`` 即表示缺失。"""
    return FeatureValue(
        feature_code=feature_code,
        feature_version=feature_version,
        as_of=as_of,
        lookback_window=lookback_window,
        value=value,
        missing_reason=missing_reason,
        source_snapshot_hash=source_snapshot_hash,
    )


def make_feature_snapshot(
    *,
    symbol: str = DEFAULT_SYMBOL,
    decision_date: date = _DEFAULT_DECISION_DATE,
    values: Mapping[str, FeatureValue] | None = None,
    content_hash: str = DEFAULT_HASH,
) -> MarketFeatureSnapshot:
    """构造一整份特征快照。"""
    if values is None:
        values = {"momentum_20": make_feature_value()}
    return MarketFeatureSnapshot(
        symbol=symbol,
        decision_date=decision_date,
        values=values,
        content_hash=content_hash,
    )


def make_execution_market(
    *,
    symbol: str = DEFAULT_SYMBOL,
    trade_date: date = _DEFAULT_EXECUTION_DATE,
    trading_day_status: TradingDayStatus = TradingDayStatus.OPEN,
    trading_status: TradingStatus = TradingStatus.TRADING,
    open_price: Decimal | None = Decimal("10.20"),
    unadjusted_close: Decimal | None = Decimal("10.20"),
    daily_amount_cny: Decimal | None = Decimal(10200000),
    upper_limit_price: Decimal | None = Decimal("11.00"),
    lower_limit_price: Decimal | None = Decimal("9.00"),
    is_initial_no_limit_period: bool = False,
) -> MarketExecutionSnapshot:
    """构造一条次日成交行情。"""
    return MarketExecutionSnapshot(
        symbol=symbol,
        trade_date=trade_date,
        trading_day_status=trading_day_status,
        trading_status=trading_status,
        open_price=open_price,
        unadjusted_close=unadjusted_close,
        daily_amount_cny=daily_amount_cny,
        upper_limit_price=upper_limit_price,
        lower_limit_price=lower_limit_price,
        is_initial_no_limit_period=is_initial_no_limit_period,
    )


def make_replay_day(
    *,
    decision_date: date = _DEFAULT_DECISION_DATE,
    decision_cutoff: datetime | None = None,
    planned_execution_date: date = _DEFAULT_EXECUTION_DATE,
    universe_snapshot_hash: str = DEFAULT_HASH,
    market_snapshot_hash: str = DEFAULT_HASH,
    feature_snapshot_hash: str = DEFAULT_HASH,
    features: Mapping[str, MarketFeatureSnapshot] | None = None,
    execution_market: Mapping[str, MarketExecutionSnapshot] | None = None,
) -> ReplayDay:
    """构造一天的完整数据（含次日成交行情）。"""
    if decision_cutoff is None:
        decision_cutoff = _DEFAULT_CUTOFF.replace(
            year=decision_date.year,
            month=decision_date.month,
            day=decision_date.day,
        )
    if features is None:
        features = {DEFAULT_SYMBOL: make_feature_snapshot()}
    if execution_market is None:
        execution_market = {
            DEFAULT_SYMBOL: make_execution_market(trade_date=planned_execution_date)
        }
    return ReplayDay(
        decision_date=decision_date,
        decision_cutoff=decision_cutoff,
        planned_execution_date=planned_execution_date,
        universe_snapshot_hash=universe_snapshot_hash,
        market_snapshot_hash=market_snapshot_hash,
        feature_snapshot_hash=feature_snapshot_hash,
        features=features,
        execution_market=execution_market,
    )


def make_portfolio(
    *,
    portfolio_id: str = "portfolio-default",
    cash_balance: Decimal = Decimal(1000000),
    frozen_cash: Decimal = Decimal(0),
    realized_pnl: Decimal = Decimal(0),
    positions: tuple[PositionState, ...] = (),
    version: int = 1,
) -> PortfolioState:
    """构造一个组合状态，默认空仓。"""
    return PortfolioState(
        portfolio_id=portfolio_id,
        cash_balance=cash_balance,
        frozen_cash=frozen_cash,
        realized_pnl=realized_pnl,
        positions=positions,
        version=version,
    )


def make_target_position(
    *,
    symbol: str = DEFAULT_SYMBOL,
    action: DecisionAction = DecisionAction.ENTER,
    target_position_pct: Decimal = Decimal("0.1"),
    sizing_version: str = "sizing-v1",
    sizing_input_hash: str = DEFAULT_HASH,
) -> TargetPosition:
    """构造一个目标仓位。"""
    return TargetPosition(
        symbol=symbol,
        action=action,
        target_position_pct=target_position_pct,
        sizing_version=sizing_version,
        sizing_input_hash=sizing_input_hash,
    )


def make_target_batch(
    *,
    decision_date: date = _DEFAULT_DECISION_DATE,
    planned_execution_date: date = _DEFAULT_EXECUTION_DATE,
    experiment_arm: ExperimentArm = ExperimentArm.A_LLM,
    targets: tuple[TargetPosition, ...] = (),
    input_hash: str = DEFAULT_HASH,
) -> TargetPositionBatch:
    """构造一个目标批次。"""
    return TargetPositionBatch(
        decision_date=decision_date,
        planned_execution_date=planned_execution_date,
        experiment_arm=experiment_arm,
        targets=targets,
        input_hash=input_hash,
    )


def make_execution_result(
    *,
    decision_date: date = _DEFAULT_DECISION_DATE,
    execution_date: date = _DEFAULT_EXECUTION_DATE,
    portfolio_after: PortfolioState | None = None,
    total_equity: Decimal = Decimal(1000000),
    cash_balance: Decimal = Decimal(1000000),
    market_value: Decimal = Decimal(0),
    gross_traded_value: Decimal = Decimal(0),
    total_fees: Decimal = Decimal(0),
    submitted_orders: int = 0,
    filled_orders: int = 0,
    rejected_orders: int = 0,
) -> BacktestExecutionResult:
    """构造一份执行结果；可故意让账目对不上以验证引擎会发现。"""
    if portfolio_after is None:
        portfolio_after = make_portfolio()
    return BacktestExecutionResult(
        decision_date=decision_date,
        execution_date=execution_date,
        portfolio_after=portfolio_after,
        total_equity=total_equity,
        cash_balance=cash_balance,
        market_value=market_value,
        gross_traded_value=gross_traded_value,
        total_fees=total_fees,
        submitted_orders=submitted_orders,
        filled_orders=filled_orders,
        rejected_orders=rejected_orders,
    )


class FakeReplayProvider:
    """按配置的日期序列逐天吐出 ReplayDay 的假行情回放器。

    ``days`` 是 ``date -> ReplayDay`` 的映射，可以包含 start_date 之前的预热日。
    ``decision_dates`` 用于覆盖返回顺序；缺省时按日期升序。故意传非升序即可
    模拟"乱序"，传空映射即可模拟"空窗口"。
    """

    def __init__(
        self,
        days: Mapping[date, ReplayDay] | None = None,
        *,
        decision_dates: Sequence[date] | None = None,
    ) -> None:
        self._days: dict[date, ReplayDay] = dict(days or {})
        if decision_dates is None:
            self._decision_dates: tuple[date, ...] = tuple(sorted(self._days))
        else:
            self._decision_dates = tuple(decision_dates)
        self.decision_dates_calls: list[tuple[date, date]] = []
        self.load_day_calls: list[date] = []
        self.warmup_dates_calls: list[tuple[date, int]] = []

    async def warmup_dates(self, before: date, count: int) -> tuple[date, ...]:
        self.warmup_dates_calls.append((before, count))
        candidates = sorted(item for item in self._decision_dates if item < before)
        return tuple(candidates[:count])

    async def decision_dates(self, start: date, end: date) -> tuple[date, ...]:
        self.decision_dates_calls.append((start, end))
        return tuple(item for item in self._decision_dates if start <= item <= end)

    async def load_day(self, decision_date: date) -> ReplayDay:
        self.load_day_calls.append(decision_date)
        return self._days[decision_date]


class FakeTargetProvider:
    """不判断、不计算，直接返回预先指定目标批次的假目标生成器。

    ``batch`` 每天返回同一批次；``batches`` 可按决策日覆盖。记录收到的每一天
    对象，以及该对象是否带次日行情，供防前视验收使用。
    """

    def __init__(
        self,
        *,
        batch: TargetPositionBatch | None = None,
        batches: Mapping[date, TargetPositionBatch] | None = None,
    ) -> None:
        if batch is None and not batches:
            raise ValueError("FakeTargetProvider 需要 batch 或 batches 至少一个")
        self._batch: TargetPositionBatch | None = batch
        self._batches: dict[date, TargetPositionBatch] = dict(batches or {})
        self.calls: list[DecisionReplayDay] = []
        self.received_has_execution_market: list[bool] = []

    async def build_targets(
        self,
        config: BacktestConfig,
        day: DecisionReplayDay,
        portfolio: PortfolioState,
    ) -> TargetPositionBatch:
        self.calls.append(day)
        self.received_has_execution_market.append(hasattr(day, "execution_market"))
        by_date = self._batches.get(day.decision_date)
        if by_date is not None:
            return by_date
        if self._batch is not None:
            return self._batch
        raise KeyError(f"没有为 {day.decision_date} 配置目标批次")


class FakeExecutionPort:
    """按配置的结果直接"成交"的假执行器，不做任何真实撮合。

    ``results`` 逐日消费；最后一个结果在后续日期重复使用。实例状态全部存在
    ``self`` 上，两个实例互不影响。
    """

    def __init__(self, *, results: Sequence[BacktestExecutionResult]) -> None:
        if not results:
            raise ValueError("FakeExecutionPort 需要至少一个结果")
        self._results: tuple[BacktestExecutionResult, ...] = tuple(results)
        self._cursor: int = 0
        self.calls: int = 0
        self.received_targets: list[TargetPositionBatch] = []
        self.received_market_dates: list[tuple[date, ...]] = []

    async def execute(
        self,
        portfolio: PortfolioState,
        targets: TargetPositionBatch,
        market: Mapping[str, MarketExecutionSnapshot],
    ) -> BacktestExecutionResult:
        self.calls += 1
        self.received_targets.append(targets)
        self.received_market_dates.append(
            tuple(snapshot.trade_date for snapshot in market.values())
        )
        index = min(self._cursor, len(self._results) - 1)
        result = self._results[index]
        self._cursor += 1
        return result
