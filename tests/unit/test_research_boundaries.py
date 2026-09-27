from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from fkqt_jevinvestor.domain.backtest import ExperimentArm
from fkqt_jevinvestor.domain.execution import ExecutionPolicy
from fkqt_jevinvestor.domain.portfolio import PositionState
from tests.unit.research_fixtures import cash
from tests.unit.test_research_strategy import FakeLlm, provider


async def test_replay_only_refuses_missing_record_and_reuses_existing(tmp_path: Path) -> None:
    from fkqt_jevinvestor.research.strategy import ExperimentTargetProvider

    strategy, replay, cfg = provider(tmp_path, ExperimentArm.B_LLM, llm=FakeLlm())
    day = (await replay.load_day(cfg.start_date)).decision_view()
    readonly = ExperimentTargetProvider(
        replay, tmp_path / "audit", strategy.strategy, replay_only=True
    )
    with pytest.raises(ValueError, match="EXPERIMENT_NOT_FROZEN"):
        await readonly.build_targets(cfg, day, cash())
    result = await strategy.build_targets(cfg, day, cash())
    assert await readonly.build_targets(cfg, day, cash()) == result


async def test_llm_failure_keeps_held_only_position_and_isolates_changed_cash(
    tmp_path: Path,
) -> None:
    strategy, replay, cfg = provider(tmp_path, ExperimentArm.B_LLM, llm=FakeLlm(True))
    day = (await replay.load_day(cfg.start_date)).decision_view()
    held = PositionState(
        symbol="000002.SZ",
        quantity=100,
        sellable_quantity=100,
        average_cost=Decimal(10),
        total_cost=Decimal(1000),
        last_price=Decimal(10),
        current_position_pct=Decimal("0.1"),
        unrealized_pnl=Decimal(0),
        holding_trading_days=1,
    )
    portfolio = cash().model_copy(update={"cash_balance": Decimal(9000), "positions": (held,)})
    result = await strategy.build_targets(cfg, day, portfolio)
    assert len(result.targets) == 3
    assert next(
        t for t in result.targets if t.symbol == "000002.SZ"
    ).target_position_pct == Decimal("0.1")
    await strategy.build_targets(
        cfg, day, portfolio.model_copy(update={"cash_balance": Decimal(8000)})
    )
    assert len(list((tmp_path / "audit" / "experiments").glob("*.json"))) == 2


def test_execution_policy_is_part_of_frozen_dataset_and_mismatch_rejected(tmp_path: Path) -> None:
    from fkqt_jevinvestor.research.dataset import DatasetStore, FrozenReplayDataProvider
    from fkqt_jevinvestor.research.execution import ResearchExecutionPort
    from tests.unit.test_research_dataset import build_day

    store = DatasetStore(tmp_path)
    path = store.publish("research-fixture", (build_day(),))
    replay = FrozenReplayDataProvider(store, path)
    with pytest.raises(ValueError, match="POLICY_MISMATCH"):
        ResearchExecutionPort(replay, ExecutionPolicy(commission_bps=Decimal(99)))


async def test_history_does_not_mix_model_or_sizing_versions(tmp_path: Path) -> None:
    from fkqt_jevinvestor.research.models import ResearchStrategyConfig
    from fkqt_jevinvestor.research.strategy import ExperimentTargetProvider

    strategy, replay, cfg = provider(tmp_path, ExperimentArm.A_RULE)
    day = (await replay.load_day(cfg.start_date)).decision_view()
    await strategy.build_targets(cfg, day, cash())
    newer = day.model_copy(update={"decision_date": date(2026, 9, 21)})
    changed = ExperimentTargetProvider(
        replay, tmp_path / "audit", ResearchStrategyConfig(llm_model="different-model")
    )
    assert changed._history(cfg, cash(), newer) == {}  # pyright: ignore[reportPrivateUsage]


async def test_history_isolates_full_config_and_refuses_same_day_branches(tmp_path: Path) -> None:
    strategy, replay, cfg = provider(tmp_path, ExperimentArm.A_RULE)
    day = (await replay.load_day(cfg.start_date)).decision_view()
    await strategy.build_targets(cfg, day, cash())
    newer = day.model_copy(update={"decision_date": date(2026, 9, 21)})
    changed = cfg.model_copy(update={"initial_cash": Decimal(200000)})
    assert strategy._history(changed, cash(), newer) == {}  # pyright: ignore[reportPrivateUsage]
    changed = cfg.model_copy(update={"start_date": date(2026, 9, 21)})
    assert strategy._history(changed, cash(), newer) == {}  # pyright: ignore[reportPrivateUsage]
    await strategy.build_targets(
        cfg, day, cash().model_copy(update={"cash_balance": Decimal(99999)})
    )
    with pytest.raises(ValueError, match="HISTORY_BRANCH_AMBIGUOUS"):
        strategy._history(cfg, cash(), newer)  # pyright: ignore[reportPrivateUsage]


async def test_frozen_later_day_prevents_history_backfill(tmp_path: Path) -> None:
    from fkqt_jevinvestor.research.dataset import DatasetStore, FrozenReplayDataProvider
    from fkqt_jevinvestor.research.models import ResearchStrategyConfig
    from fkqt_jevinvestor.research.strategy import ExperimentTargetProvider
    from tests.unit.research_fixtures import config, two_days

    store = DatasetStore(tmp_path / "datasets")
    replay = FrozenReplayDataProvider(store, store.publish("research-fixture", two_days()))
    cfg = config(ExperimentArm.B_LLM, replay.manifest.dataset_hash).model_copy(
        update={"end_date": date(2026, 9, 21)}
    )
    llm = FakeLlm()
    strategy = ExperimentTargetProvider(
        replay, tmp_path / "audit", ResearchStrategyConfig(), llm=llm
    )
    later = (await replay.load_day(cfg.end_date)).decision_view()
    result = await strategy.build_targets(cfg, later, cash())
    earlier = (await replay.load_day(cfg.start_date)).decision_view()
    with pytest.raises(ValueError, match="HISTORY_BACKFILL_FORBIDDEN"):
        await strategy.build_targets(cfg, earlier, cash())
    assert await strategy.build_targets(cfg, later, cash()) == result
    assert len(llm.requests) == 2
