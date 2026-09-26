"""回测引擎验收测试。

覆盖契约的 13 个稳定错误码、预热期、决策视图隔离、结果哈希与确定性。
期望值按规格手工计算。
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from fkqt_jevinvestor.backtest.engine import BacktestEngine, BacktestError
from fkqt_jevinvestor.backtest.runner import run_experiment_arms
from fkqt_jevinvestor.backtest.serialization import canonical_result_json
from fkqt_jevinvestor.domain.backtest import (
    BacktestExecutionResult,
    DecisionAction,
    ExperimentArm,
    ReplayDay,
    TargetPosition,
    TargetPositionBatch,
)
from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot
from fkqt_jevinvestor.domain.portfolio import PortfolioState
from tests.backtest.fixtures import (
    DEFAULT_SYMBOL,
    FakeReplayProvider,
    FakeTargetProvider,
    make_config,
    make_execution_market,
    make_execution_result,
    make_feature_snapshot,
    make_feature_value,
    make_replay_day,
    make_target_batch,
    make_target_position,
)
from tests.backtest.fixtures import (
    FakeExecutionPort as RawExecutionPort,
)


class FakeExecutionPort(RawExecutionPort):
    async def execute(
        self,
        portfolio: PortfolioState,
        targets: TargetPositionBatch,
        market: Mapping[str, MarketExecutionSnapshot],
    ) -> BacktestExecutionResult:
        # 成功样本绑定当前输入身份；故意错误结果仍由独立guard测试使用原始假件。
        result = await super().execute(portfolio, targets, market)
        return result.model_copy(
            update={
                "decision_date": targets.decision_date,
                "execution_date": targets.planned_execution_date,
                "portfolio_after": result.portfolio_after.model_copy(
                    update={
                        "portfolio_id": portfolio.portfolio_id,
                    }
                ),
            }
        )


def _keep_target() -> TargetPosition:
    return make_target_position(symbol=DEFAULT_SYMBOL, action=DecisionAction.KEEP)


async def test_two_day_normal_backtest() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    d3 = date(2026, 1, 7)
    replay = FakeReplayProvider(
        days={
            d1: make_replay_day(decision_date=d1, planned_execution_date=d2),
            d2: make_replay_day(decision_date=d2, planned_execution_date=d3),
        }
    )
    targets = FakeTargetProvider(
        batches={
            d1: make_target_batch(
                decision_date=d1, planned_execution_date=d2, targets=(_keep_target(),)
            ),
            d2: make_target_batch(
                decision_date=d2, planned_execution_date=d3, targets=(_keep_target(),)
            ),
        }
    )
    port = FakeExecutionPort(results=(make_execution_result(), make_execution_result()))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    result = await engine.run(make_config(start_date=d1, end_date=d2))
    assert len(result.daily_records) == 2
    for record in result.daily_records:
        assert abs(record.total_equity - (record.cash_balance + record.market_value)) <= Decimal(
            "0.01"
        )


async def test_warmup_produces_no_records_or_target_calls() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    d3 = date(2026, 1, 7)
    warmup_days = [d1 - timedelta(days=60 - i) for i in range(60)]
    days: dict[date, ReplayDay] = {}
    for index, wd in enumerate(warmup_days):
        days[wd] = make_replay_day(
            decision_date=wd,
            planned_execution_date=warmup_days[index + 1] if index + 1 < len(warmup_days) else d1,
            features={},
        )
    days[d1] = make_replay_day(decision_date=d1, planned_execution_date=d2)
    days[d2] = make_replay_day(decision_date=d2, planned_execution_date=d3)
    replay = FakeReplayProvider(days=days)
    targets = FakeTargetProvider(
        batches={
            d1: make_target_batch(
                decision_date=d1, planned_execution_date=d2, targets=(_keep_target(),)
            ),
            d2: make_target_batch(
                decision_date=d2, planned_execution_date=d3, targets=(_keep_target(),)
            ),
        }
    )
    port = FakeExecutionPort(results=(make_execution_result(), make_execution_result()))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    result = await engine.run(make_config(start_date=d1, end_date=d2, warmup_trading_days=60))
    assert len(result.daily_records) == 2
    assert len(targets.calls) == 2
    # 预热日数据确实被读取：被读天数 = 预热 60 + 正式 2
    assert len(replay.load_day_calls) == 62


async def test_weekend_execution_uses_provider_date() -> None:
    friday = date(2026, 1, 9)
    monday = date(2026, 1, 12)
    day = make_replay_day(
        decision_date=friday,
        planned_execution_date=monday,
        execution_market={DEFAULT_SYMBOL: make_execution_market(trade_date=monday)},
    )
    replay = FakeReplayProvider(days={friday: day})
    targets = FakeTargetProvider(
        batch=make_target_batch(
            decision_date=friday,
            planned_execution_date=monday,
            targets=(_keep_target(),),
        )
    )
    port = FakeExecutionPort(results=(make_execution_result(execution_date=monday),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    result = await engine.run(make_config(start_date=friday, end_date=friday))
    assert result.daily_records[0].execution_date == monday
    assert port.received_market_dates == [(monday,)]


async def test_future_feature_fails_whole_run() -> None:
    d1 = date(2026, 1, 5)
    cutoff = datetime(2026, 1, 5, 15, 0, 0, tzinfo=UTC)
    future = datetime(2026, 1, 6, 9, 30, 0, tzinfo=UTC)
    feature = make_feature_value(as_of=future)
    snapshot = make_feature_snapshot(values={"momentum_20": feature})
    day = make_replay_day(
        decision_date=d1,
        decision_cutoff=cutoff,
        features={DEFAULT_SYMBOL: snapshot},
    )
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(batch=make_target_batch(decision_date=d1))
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    with pytest.raises(BacktestError, match="POINT_IN_TIME_VIOLATION"):
        await engine.run(make_config(start_date=d1, end_date=d1))


async def test_target_date_mismatch_fails() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    day = make_replay_day(decision_date=d1, planned_execution_date=d2)
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(
        batch=make_target_batch(decision_date=date(2026, 1, 7), planned_execution_date=d2)
    )
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    with pytest.raises(BacktestError, match="TARGET_DATE_MISMATCH"):
        await engine.run(make_config(start_date=d1, end_date=d1))


async def test_target_arm_mismatch_fails() -> None:
    d1 = date(2026, 1, 5)
    day = make_replay_day(decision_date=d1)
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(
        batch=make_target_batch(decision_date=d1, experiment_arm=ExperimentArm.A_LLM)
    )
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    config = make_config(start_date=d1, end_date=d1, experiment_arm=ExperimentArm.B_JEV_LLM)
    with pytest.raises(BacktestError, match="TARGET_EXPERIMENT_ARM_MISMATCH"):
        await engine.run(config)


async def test_empty_window_fails() -> None:
    replay = FakeReplayProvider(days={})
    targets = FakeTargetProvider(batch=make_target_batch())
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    config = make_config(start_date=date(2026, 1, 5), end_date=date(2026, 1, 6))
    with pytest.raises(BacktestError, match="EMPTY_BACKTEST_WINDOW"):
        await engine.run(config)


async def test_replay_is_deterministic() -> None:
    d1 = date(2026, 1, 5)
    day = make_replay_day(decision_date=d1)
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(
        batch=make_target_batch(decision_date=d1, targets=(_keep_target(),))
    )
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    config = make_config(start_date=d1, end_date=d1)
    first = await engine.run(config)
    second = await engine.run(config)
    assert canonical_result_json(first) == canonical_result_json(second)
    assert first.summary.result_hash == second.summary.result_hash


async def test_zero_volatility_sharpe_none() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    d3 = date(2026, 1, 7)
    replay = FakeReplayProvider(
        days={
            d1: make_replay_day(decision_date=d1, planned_execution_date=d2),
            d2: make_replay_day(decision_date=d2, planned_execution_date=d3),
        }
    )
    targets = FakeTargetProvider(
        batches={
            d1: make_target_batch(
                decision_date=d1, planned_execution_date=d2, targets=(_keep_target(),)
            ),
            d2: make_target_batch(
                decision_date=d2, planned_execution_date=d3, targets=(_keep_target(),)
            ),
        }
    )
    port = FakeExecutionPort(results=(make_execution_result(), make_execution_result()))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    result = await engine.run(make_config(start_date=d1, end_date=d2))
    assert result.summary.sharpe_ratio is None
    assert result.summary.sortino_ratio is None


async def test_no_orders_fill_rate_none() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    d3 = date(2026, 1, 7)
    replay = FakeReplayProvider(
        days={
            d1: make_replay_day(decision_date=d1, planned_execution_date=d2),
            d2: make_replay_day(decision_date=d2, planned_execution_date=d3),
        }
    )
    targets = FakeTargetProvider(
        batches={
            d1: make_target_batch(
                decision_date=d1, planned_execution_date=d2, targets=(_keep_target(),)
            ),
            d2: make_target_batch(
                decision_date=d2, planned_execution_date=d3, targets=(_keep_target(),)
            ),
        }
    )
    port = FakeExecutionPort(results=(make_execution_result(), make_execution_result()))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    result = await engine.run(make_config(start_date=d1, end_date=d2))
    assert result.summary.fill_rate is None


async def test_fairness_rejects_whole_batch() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    config_a = make_config(
        run_id="run-a", experiment_arm=ExperimentArm.A_LLM, start_date=d1, end_date=d2
    )
    config_b = make_config(
        run_id="run-b",
        experiment_arm=ExperimentArm.B_JEV_LLM,
        start_date=d1,
        end_date=d2,
        dataset_hash="1" * 64,
    )
    replay = FakeReplayProvider(days={d1: make_replay_day(decision_date=d1)})
    providers = {
        ExperimentArm.A_LLM: FakeTargetProvider(
            batch=make_target_batch(experiment_arm=ExperimentArm.A_LLM)
        ),
        ExperimentArm.B_JEV_LLM: FakeTargetProvider(
            batch=make_target_batch(experiment_arm=ExperimentArm.B_JEV_LLM)
        ),
    }
    ports: list[FakeExecutionPort] = []

    def execution_factory() -> FakeExecutionPort:
        port = FakeExecutionPort(results=(make_execution_result(),))
        ports.append(port)
        return port

    with pytest.raises(BacktestError):
        await run_experiment_arms((config_a, config_b), replay, providers, execution_factory)
    assert ports == []


async def test_arm_state_isolation() -> None:
    d1 = date(2026, 1, 5)
    arms = (
        ExperimentArm.A_LLM,
        ExperimentArm.B_JEV_LLM,
        ExperimentArm.C_JEV_DIRECT,
        ExperimentArm.D_RULE,
    )
    configs = tuple(
        make_config(run_id=f"run-{arm.value}", experiment_arm=arm, start_date=d1, end_date=d1)
        for arm in arms
    )
    replay = FakeReplayProvider(days={d1: make_replay_day(decision_date=d1)})
    providers = {
        arm: FakeTargetProvider(
            batch=make_target_batch(decision_date=d1, experiment_arm=arm, targets=(_keep_target(),))
        )
        for arm in arms
    }
    ports: list[FakeExecutionPort] = []

    def execution_factory() -> FakeExecutionPort:
        port = FakeExecutionPort(results=(make_execution_result(),))
        ports.append(port)
        return port

    await run_experiment_arms(configs, replay, providers, execution_factory)
    assert len(ports) == 4
    assert all(port.calls == 1 for port in ports)


async def test_invalid_backtest_window_fails() -> None:
    replay = FakeReplayProvider(days={})
    targets = FakeTargetProvider(batch=make_target_batch())
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    config = make_config(start_date=date(2026, 1, 6), end_date=date(2026, 1, 5))
    with pytest.raises(BacktestError, match="INVALID_BACKTEST_WINDOW"):
        await engine.run(config)


async def test_decision_dates_not_strictly_ordered_fails() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    replay = FakeReplayProvider(
        days={
            d1: make_replay_day(decision_date=d1),
            d2: make_replay_day(decision_date=d2),
        },
        decision_dates=[d2, d1],
    )
    targets = FakeTargetProvider(batch=make_target_batch())
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    config = make_config(start_date=d1, end_date=d2)
    with pytest.raises(BacktestError, match="DECISION_DATES_NOT_STRICTLY_ORDERED"):
        await engine.run(config)


async def test_replay_day_date_mismatch_fails() -> None:
    d1 = date(2026, 1, 5)
    wrong = date(2026, 1, 6)
    day = make_replay_day(decision_date=wrong)
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(batch=make_target_batch())
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    config = make_config(start_date=d1, end_date=d1)
    with pytest.raises(BacktestError, match="REPLAY_DAY_DATE_MISMATCH"):
        await engine.run(config)


async def test_target_sizing_version_mismatch_fails() -> None:
    d1 = date(2026, 1, 5)
    day = make_replay_day(decision_date=d1)
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(
        batch=make_target_batch(
            decision_date=d1,
            targets=(make_target_position(sizing_version="sizing-other"),),
        )
    )
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    config = make_config(start_date=d1, end_date=d1)
    with pytest.raises(BacktestError, match="TARGET_SIZING_VERSION_MISMATCH"):
        await engine.run(config)


async def test_execution_date_not_after_decision_date_fails() -> None:
    d1 = date(2026, 1, 5)
    day = make_replay_day(decision_date=d1, planned_execution_date=d1)
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(
        batch=make_target_batch(decision_date=d1, planned_execution_date=d1)
    )
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    with pytest.raises(BacktestError, match="EXECUTION_DATE_NOT_AFTER_DECISION_DATE"):
        await engine.run(make_config(start_date=d1, end_date=d1))


async def test_decision_cutoff_date_mismatch_fails() -> None:
    d1 = date(2026, 1, 5)
    next_day_cutoff = datetime(2026, 1, 6, 15, 0, 0, tzinfo=UTC)
    day = make_replay_day(decision_date=d1, decision_cutoff=next_day_cutoff)
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(batch=make_target_batch(decision_date=d1))
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    with pytest.raises(BacktestError, match="DECISION_CUTOFF_DATE_MISMATCH"):
        await engine.run(make_config(start_date=d1, end_date=d1))


async def test_insufficient_warmup_data_fails() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    w0 = date(2026, 1, 4)
    replay = FakeReplayProvider(
        days={
            w0: make_replay_day(decision_date=w0, planned_execution_date=d1, features={}),
            d1: make_replay_day(decision_date=d1, planned_execution_date=d2),
        }
    )
    targets = FakeTargetProvider(batch=make_target_batch(decision_date=d1))
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    config = make_config(start_date=d1, end_date=d1, warmup_trading_days=3)
    with pytest.raises(BacktestError, match="INSUFFICIENT_WARMUP_DATA"):
        await engine.run(config)


async def test_target_symbol_coverage_mismatch_fails() -> None:
    d1 = date(2026, 1, 5)
    features = {
        DEFAULT_SYMBOL: make_feature_snapshot(symbol=DEFAULT_SYMBOL),
        "000001.SZ": make_feature_snapshot(symbol="000001.SZ"),
    }
    day = make_replay_day(decision_date=d1, features=features)
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(
        batch=make_target_batch(
            decision_date=d1,
            targets=(_keep_target(),),
        )
    )
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    with pytest.raises(BacktestError, match="TARGET_SYMBOL_COVERAGE_MISMATCH"):
        await engine.run(make_config(start_date=d1, end_date=d1))


async def test_portfolio_equity_depleted_fails() -> None:
    d1 = date(2026, 1, 5)
    day = make_replay_day(decision_date=d1)
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(
        batch=make_target_batch(decision_date=d1, targets=(_keep_target(),))
    )
    port = FakeExecutionPort(results=(make_execution_result(total_equity=Decimal(0)),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    with pytest.raises(BacktestError, match="PORTFOLIO_EQUITY_DEPLETED"):
        await engine.run(make_config(start_date=d1, end_date=d1))


async def test_engine_passes_decision_view_without_execution_market() -> None:
    d1 = date(2026, 1, 5)
    day = make_replay_day(decision_date=d1)
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(
        batch=make_target_batch(decision_date=d1, targets=(_keep_target(),))
    )
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    await engine.run(make_config(start_date=d1, end_date=d1))
    assert targets.received_has_execution_market == [False]
