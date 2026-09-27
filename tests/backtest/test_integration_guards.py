from datetime import date
from decimal import Decimal

import pytest

from fkqt_jevinvestor.backtest.engine import BacktestEngine, BacktestError
from fkqt_jevinvestor.backtest.runner import run_experiment_arms
from tests.backtest.fixtures import (
    FakeExecutionPort,
    FakeReplayProvider,
    FakeTargetProvider,
    make_config,
    make_execution_result,
    make_portfolio,
    make_replay_day,
    make_target_batch,
    make_target_position,
)


@pytest.mark.parametrize("warmup", [False, True])
async def test_engine_rejects_discontinuous_frozen_day_chain(warmup: bool) -> None:
    d1, d2, d3, d4 = (date(2026, 1, n) for n in (5, 6, 7, 8))
    replay = FakeReplayProvider(
        days={
            d1: make_replay_day(decision_date=d1, planned_execution_date=d2),
            d3: make_replay_day(decision_date=d3, planned_execution_date=d4),
        }
    )
    batches = {
        d: make_target_batch(
            decision_date=d, planned_execution_date=e, targets=(make_target_position(),)
        )
        for d, e in ((d1, d2), (d3, d4))
    }
    targets = FakeTargetProvider(batches=batches)
    port = FakeExecutionPort(
        results=(
            make_execution_result(
                portfolio_after=make_portfolio(
                    portfolio_id="run-default",
                )
            ),
        )
    )
    with pytest.raises(BacktestError, match="REPLAY_CALENDAR_CHAIN_MISMATCH"):
        await BacktestEngine(replay, targets, port).run(
            make_config(
                start_date=d3 if warmup else d1,
                end_date=d3,
                warmup_trading_days=int(warmup),
            )
        )
    assert port.calls == (0 if warmup else 1)


async def test_engine_rejects_duplicate_targets_before_execution() -> None:
    replay = FakeReplayProvider(days={date(2026, 1, 5): make_replay_day()})
    target = make_target_position()
    targets = FakeTargetProvider(batch=make_target_batch(targets=(target, target)))
    port = FakeExecutionPort(results=(make_execution_result(),))
    with pytest.raises(BacktestError, match="TARGET_SYMBOL_COVERAGE_MISMATCH"):
        await BacktestEngine(replay, targets, port).run(make_config())
    assert port.calls == 0


@pytest.mark.parametrize("defect", ["date", "portfolio", "equity", "cash", "positions"])
async def test_engine_rejects_invalid_execution_identity_and_accounting(defect: str) -> None:
    replay = FakeReplayProvider(days={date(2026, 1, 5): make_replay_day()})
    targets = FakeTargetProvider(batch=make_target_batch(targets=(make_target_position(),)))
    result = make_execution_result(portfolio_after=make_portfolio(portfolio_id="run-default"))
    if defect == "date":
        result = result.model_copy(update={"execution_date": date(1999, 1, 1)})
    elif defect == "portfolio":
        result = result.model_copy(
            update={
                "portfolio_after": result.portfolio_after.model_copy(
                    update={"portfolio_id": "different-account"},
                )
            }
        )
    elif defect == "equity":
        result = result.model_copy(update={"total_equity": Decimal(999999)})
    elif defect == "cash":
        result = result.model_copy(
            update={
                "portfolio_after": result.portfolio_after.model_copy(
                    update={"cash_balance": Decimal(999999)},
                )
            }
        )
    else:
        result = result.model_copy(
            update={"market_value": Decimal(1), "total_equity": Decimal(1000001)}
        )
    port = FakeExecutionPort(results=(result,))
    with pytest.raises(BacktestError, match="EXECUTION_RESULT_(IDENTITY|ACCOUNTING)_MISMATCH"):
        await BacktestEngine(replay, targets, port).run(make_config())


async def test_runner_rejects_duplicate_group_before_any_execution() -> None:
    replay = FakeReplayProvider(days={date(2026, 1, 5): make_replay_day()})
    cfg = make_config()
    targets = FakeTargetProvider(batch=make_target_batch(targets=(make_target_position(),)))
    port = FakeExecutionPort(results=(make_execution_result(),))
    with pytest.raises(BacktestError, match="EXPERIMENT_CONFIG_MISMATCH"):
        await run_experiment_arms((cfg, cfg), replay, {cfg.experiment_arm: targets}, lambda: port)
    assert port.calls == 0
    assert targets.calls == []


async def test_engine_rejects_provider_dates_outside_requested_window() -> None:
    d1, d2 = date(2026, 1, 5), date(2026, 1, 6)

    class OutOfWindowReplay(FakeReplayProvider):
        async def decision_dates(self, start: date, end: date) -> tuple[date, ...]:
            return (d1,)

    replay = OutOfWindowReplay(days={d1: make_replay_day()})
    targets = FakeTargetProvider(batch=make_target_batch(targets=(make_target_position(),)))
    port = FakeExecutionPort(results=(make_execution_result(),))
    with pytest.raises(BacktestError, match="DECISION_DATES_OUTSIDE_WINDOW"):
        await BacktestEngine(replay, targets, port).run(make_config(start_date=d2, end_date=d2))
    assert targets.calls == []
    assert port.calls == 0
