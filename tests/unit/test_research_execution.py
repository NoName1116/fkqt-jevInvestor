from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from fkqt_jevinvestor.domain.backtest import (
    DecisionAction,
    ExperimentArm,
    TargetPosition,
    TargetPositionBatch,
)
from fkqt_jevinvestor.domain.execution import ExecutionPolicy
from tests.unit.research_fixtures import cash, config
from tests.unit.test_research_dataset import build_day


def batch() -> TargetPositionBatch:
    return TargetPositionBatch(
        decision_date=date(2026, 9, 18),
        planned_execution_date=date(2026, 9, 21),
        experiment_arm=ExperimentArm.A_RULE,
        targets=(
            TargetPosition(
                symbol="600000.SH",
                action=DecisionAction.ENTER,
                target_position_pct=Decimal("0.1"),
                sizing_version="position-sizing-v1",
                sizing_input_hash="c" * 64,
            ),
        ),
        input_hash="b" * 64,
    )


async def test_execution_reuses_real_cost_and_is_idempotent(tmp_path: Path) -> None:
    from fkqt_jevinvestor.research.dataset import DatasetStore, FrozenReplayDataProvider
    from fkqt_jevinvestor.research.execution import ResearchExecutionPort

    day = build_day()
    store = DatasetStore(tmp_path)
    provider = FrozenReplayDataProvider(store, store.publish("research-fixture", (day,)))
    port = ResearchExecutionPort(provider, ExecutionPolicy())
    result = await port.execute(cash(), batch(), day.execution.snapshots)
    assert result.total_fees == Decimal("3.00")
    assert result.gross_traded_value == Decimal("10005.00")
    assert result.cash_balance == Decimal("89992.00")
    assert result.total_equity == Decimal("100992.00")
    assert result.filled_orders == 1
    assert result.portfolio_after.positions[0].sellable_quantity == 0
    assert result.portfolio_after.positions[0].holding_trading_days == 1
    assert await port.execute(cash(), batch(), day.execution.snapshots) == result


async def test_execution_rejects_mismatched_prices_without_mutating_portfolio(
    tmp_path: Path,
) -> None:
    from fkqt_jevinvestor.research.dataset import DatasetStore, FrozenReplayDataProvider
    from fkqt_jevinvestor.research.execution import ResearchExecutionPort

    day = build_day()
    store = DatasetStore(tmp_path)
    port = ResearchExecutionPort(
        FrozenReplayDataProvider(store, store.publish("research-fixture", (day,))),
        ExecutionPolicy(),
    )
    with pytest.raises(ValueError, match="EXECUTION_INPUT_MISMATCH"):
        await port.execute(cash(), batch(), {})
    assert (await port.execute(cash(), batch(), day.execution.snapshots)).filled_orders == 1


async def test_recorded_target_requires_exact_portfolio(tmp_path: Path) -> None:
    from fkqt_jevinvestor.research.recorded import FrozenRecordedTargetProvider, RecordedTargetV1

    day = build_day().replay_day().decision_view()
    cfg = config(ExperimentArm.C_JEV_LLM, "d" * 64)
    target = batch().model_copy(update={"experiment_arm": ExperimentArm.C_JEV_LLM})
    record = RecordedTargetV1(
        dataset_id=cfg.dataset_id,
        dataset_hash=cfg.dataset_hash,
        day=day,
        portfolio=cash(),
        targets=target,
    )
    path = record.save(tmp_path)
    provider = FrozenRecordedTargetProvider({day.decision_date: path})
    assert await provider.build_targets(cfg, day, cash()) == target
    with pytest.raises(ValueError, match="PORTFOLIO_MISMATCH"):
        await provider.build_targets(
            cfg, day, cash().model_copy(update={"cash_balance": Decimal(99999)})
        )


async def test_continuous_two_session_fifo_sell_uses_t_plus_one_and_same_costs(
    tmp_path: Path,
) -> None:
    from fkqt_jevinvestor.research.dataset import DatasetStore, FrozenReplayDataProvider
    from fkqt_jevinvestor.research.execution import ResearchExecutionPort
    from tests.unit.research_fixtures import two_days

    days = two_days()
    store = DatasetStore(tmp_path)
    replay = FrozenReplayDataProvider(store, store.publish("research-fixture", days))
    port = ResearchExecutionPort(replay, ExecutionPolicy())
    first = await port.execute(cash(), batch(), days[0].execution.snapshots)
    assert first.portfolio_after.positions[0].sellable_quantity == 0
    second_batch = batch().model_copy(
        update={
            "decision_date": date(2026, 9, 21),
            "planned_execution_date": date(2026, 9, 22),
            "targets": (
                batch()
                .targets[0]
                .model_copy(
                    update={"action": DecisionAction.EXIT, "target_position_pct": Decimal(0)}
                ),
            ),
        }
    )
    second = await port.execute(first.portfolio_after, second_batch, days[1].execution.snapshots)
    assert second.portfolio_after.positions == ()
    assert second.cash_balance == Decimal("99979.00")
    assert second.total_fees == Decimal("8.00")
    assert second.portfolio_after.realized_pnl == Decimal("-21.00")


async def test_holding_days_include_last_execution_day(tmp_path: Path) -> None:
    from fkqt_jevinvestor.research.dataset import DatasetStore, FrozenReplayDataProvider
    from fkqt_jevinvestor.research.execution import ResearchExecutionPort
    from tests.unit.research_fixtures import two_days

    days = two_days()
    store = DatasetStore(tmp_path)
    replay = FrozenReplayDataProvider(store, store.publish("research-fixture", days))
    port = ResearchExecutionPort(replay, ExecutionPolicy())
    first = await port.execute(cash(), batch(), days[0].execution.snapshots)
    hold = batch().model_copy(
        update={
            "decision_date": date(2026, 9, 21),
            "planned_execution_date": date(2026, 9, 22),
            "targets": (
                batch()
                .targets[0]
                .model_copy(
                    update={
                        "action": DecisionAction.NO_SIGNAL,
                        "target_position_pct": first.portfolio_after.positions[
                            0
                        ].current_position_pct,
                    }
                ),
            ),
        }
    )
    second = await port.execute(first.portfolio_after, hold, days[1].execution.snapshots)
    assert first.portfolio_after.positions[0].holding_trading_days == 1
    assert second.portfolio_after.positions[0].holding_trading_days == 2
