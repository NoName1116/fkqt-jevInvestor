"""日频回测事件循环。

只按交易日推进冻结数据、调用目标与执行端口、累计指标，不依赖网络、数据库
或任何外部服务；金额与比例一律 Decimal，不使用当前时间或随机数。
"""

from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable

from fkqt_jevinvestor.backtest.metrics import build_daily_record, summarize
from fkqt_jevinvestor.domain.backtest import (
    BacktestConfig,
    BacktestResult,
    DailyBacktestRecord,
    ExecutionPort,
    ReplayDataProvider,
    ReplayDay,
    TargetPositionBatch,
    TargetProvider,
)
from fkqt_jevinvestor.domain.portfolio import PortfolioState


class BacktestError(RuntimeError):
    """回测过程中可预期的失败；消息里包含稳定错误码。"""


@runtime_checkable
class CandidateQueueReplayProvider(Protocol):
    def candidate_symbols(self, decision_date: date) -> tuple[str, ...]: ...


class BacktestEngine:
    """按交易日推进冻结数据、调用目标与执行端口、累计指标的日频回测引擎。"""

    def __init__(
        self,
        replay: ReplayDataProvider,
        targets: TargetProvider,
        execution: ExecutionPort,
    ) -> None:
        self._replay = replay
        self._targets = targets
        self._execution = execution

    async def run(self, config: BacktestConfig) -> BacktestResult:
        # 1. 校验时间窗口
        if config.start_date > config.end_date:
            raise BacktestError("INVALID_BACKTEST_WINDOW")

        # 2. 取决策日
        dates = await self._replay.decision_dates(config.start_date, config.end_date)
        if list(dates) != sorted(dates) or len(dates) != len(set(dates)):
            raise BacktestError("DECISION_DATES_NOT_STRICTLY_ORDERED")
        if not dates:
            raise BacktestError("EMPTY_BACKTEST_WINDOW")
        if any(not config.start_date <= item <= config.end_date for item in dates):
            raise BacktestError("DECISION_DATES_OUTSIDE_WINDOW")

        # 3. 预热期：真正加载并校验，但不产生记录、不调目标、不调执行、不推进状态
        warmup = await self._replay.warmup_dates(config.start_date, config.warmup_trading_days)
        # 3a. 校验：严格升序、无重复、每个早于起始日、条数等于 count
        if list(warmup) != sorted(warmup) or len(warmup) != len(set(warmup)):
            raise BacktestError("INSUFFICIENT_WARMUP_DATA")
        if any(item >= config.start_date for item in warmup):
            raise BacktestError("INSUFFICIENT_WARMUP_DATA")
        if len(warmup) != config.warmup_trading_days:
            raise BacktestError("INSUFFICIENT_WARMUP_DATA")
        previous_day: ReplayDay | None = None
        for warmup_date in warmup:
            warmup_day = await self._replay.load_day(warmup_date)
            self._validate_replay_day(warmup_day, warmup_date)
            if previous_day is not None and previous_day.planned_execution_date != warmup_date:
                raise BacktestError("REPLAY_CALENDAR_CHAIN_MISMATCH")
            previous_day = warmup_day

        portfolio = self._initial_portfolio(config)
        previous_equity = config.initial_cash
        running_peak = config.initial_cash
        records: list[DailyBacktestRecord] = []

        for decision_date in dates:
            day = await self._replay.load_day(decision_date)
            self._validate_replay_day(day, decision_date)
            if previous_day is not None and previous_day.planned_execution_date != decision_date:
                raise BacktestError("REPLAY_CALENDAR_CHAIN_MISMATCH")
            previous_day = day

            # 8. 用不含次日行情的决策视图调用目标生成器
            batch = await self._targets.build_targets(config, day.decision_view(), portfolio)

            # 9. 校验目标批次（日期、实验组、仓位版本、覆盖率）
            candidates = (
                self._replay.candidate_symbols(decision_date)
                if isinstance(self._replay, CandidateQueueReplayProvider)
                else None
            )
            self._validate_targets(config, day, batch, portfolio, candidates)

            # 10. 用次日成交行情调用执行端口
            result = await self._execution.execute(portfolio, batch, day.execution_market)

            # 10a. 权益归零立即终止整次回测
            if result.total_equity == 0:
                raise BacktestError("PORTFOLIO_EQUITY_DEPLETED")
            after = result.portfolio_after
            if (
                result.decision_date != day.decision_date
                or result.execution_date != day.planned_execution_date
                or after.portfolio_id != portfolio.portfolio_id
            ):
                raise BacktestError("EXECUTION_RESULT_IDENTITY_MISMATCH")
            if (
                result.total_equity != result.cash_balance + result.market_value
                or after.cash_balance != result.cash_balance
                or after.total_equity != result.total_equity
            ):
                raise BacktestError("EXECUTION_RESULT_ACCOUNTING_MISMATCH")

            # 11. 按新签名生成逐日记录，并推进状态与运行峰值
            records.append(
                build_daily_record(
                    previous_equity,
                    config.initial_cash,
                    running_peak,
                    result,
                )
            )
            portfolio = result.portfolio_after
            previous_equity = result.total_equity
            running_peak = max(running_peak, result.total_equity)

        # 12. 汇总（summarize 内部计算 config_hash 与 result_hash）
        summary = summarize(config, tuple(records))

        return BacktestResult(
            config=config,
            daily_records=tuple(records),
            summary=summary,
        )

    @staticmethod
    def _initial_portfolio(config: BacktestConfig) -> PortfolioState:
        # portfolio_id 取 run_id、现金取初始资金、其余取零值/空仓。
        return PortfolioState(
            portfolio_id=config.run_id,
            cash_balance=config.initial_cash,
            frozen_cash=Decimal(0),
            realized_pnl=Decimal(0),
            positions=(),
            version=1,
        )

    @staticmethod
    def _validate_replay_day(day: ReplayDay, decision_date: date) -> None:
        # 4b. 回放日的决策日期与请求一致
        if day.decision_date != decision_date:
            raise BacktestError("REPLAY_DAY_DATE_MISMATCH")
        # 5. 计划执行日必须晚于决策日
        if day.planned_execution_date <= day.decision_date:
            raise BacktestError("EXECUTION_DATE_NOT_AFTER_DECISION_DATE")
        # 6. 截止时间必须落在决策日当天
        if day.decision_cutoff.date() != day.decision_date:
            raise BacktestError("DECISION_CUTOFF_DATE_MISMATCH")
        # 7. 防前视：每个特征值的 as_of 不得晚于截止时间
        cutoff = day.decision_cutoff
        for snapshot in day.features.values():
            for feature in snapshot.values.values():
                if feature.as_of > cutoff:
                    raise BacktestError("POINT_IN_TIME_VIOLATION")

    @staticmethod
    def _validate_targets(
        config: BacktestConfig,
        day: ReplayDay,
        batch: TargetPositionBatch,
        portfolio: PortfolioState,
        candidate_symbols: tuple[str, ...] | None = None,
    ) -> None:
        # 9a. 批次日期与当天一致
        if (
            batch.decision_date != day.decision_date
            or batch.planned_execution_date != day.planned_execution_date
        ):
            raise BacktestError("TARGET_DATE_MISMATCH")
        # 9b. 实验组一致
        if batch.experiment_arm != config.experiment_arm:
            raise BacktestError("TARGET_EXPERIMENT_ARM_MISMATCH")
        # 9c. 仓位版本一致
        for target in batch.targets:
            if target.sizing_version != config.sizing_version:
                raise BacktestError("TARGET_SIZING_VERSION_MISMATCH")
        # 9d. 覆盖率：批次恰好覆盖「特征证券 ∪ 当前持仓证券」（持仓按数量大于 0 计）
        if candidate_symbols is not None and not set(candidate_symbols).issubset(day.features):
            raise BacktestError("REPLAY_CANDIDATE_COVERAGE_MISMATCH")
        expected_symbols = set(day.features if candidate_symbols is None else candidate_symbols) | {
            position.symbol for position in portfolio.positions if position.quantity > 0
        }
        actual_symbols = {target.symbol for target in batch.targets}
        if len(actual_symbols) != len(batch.targets) or actual_symbols != expected_symbols:
            raise BacktestError("TARGET_SYMBOL_COVERAGE_MISMATCH")
