import inspect
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from fkqt_jevinvestor.domain.backtest import (
    BacktestConfig,
    DecisionAction,
    DecisionReplayDay,
    ExperimentArm,
    TargetPosition,
)
from fkqt_jevinvestor.domain.portfolio import PortfolioState
from fkqt_jevinvestor.services.frozen_c_group_target import (
    FrozenCGroupTargetProvider,
    FrozenTargetBundleV1,
    FrozenTargetStore,
)


def _day(day: date, suffix: str) -> DecisionReplayDay:
    return DecisionReplayDay(
        decision_date=day,
        decision_cutoff=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
        planned_execution_date=date.fromordinal(day.toordinal() + 1),
        universe_snapshot_hash=("a" if suffix == "1" else "d") * 64,
        market_snapshot_hash=("b" if suffix == "1" else "e") * 64,
        feature_snapshot_hash=("c" if suffix == "1" else "f") * 64,
        features={},
    )


def _bundle(dataset_id: str, dataset_hash: str, day: DecisionReplayDay) -> FrozenTargetBundleV1:
    return FrozenTargetBundleV1.create(
        dataset_id=dataset_id,
        dataset_hash=dataset_hash,
        decision_date=day.decision_date,
        planned_execution_date=day.planned_execution_date,
        universe_snapshot_hash=day.universe_snapshot_hash,
        market_snapshot_hash=day.market_snapshot_hash,
        feature_snapshot_hash=day.feature_snapshot_hash,
        sizing_version="position-sizing-v1",
        targets=(
            TargetPosition(
                symbol="600000.SH",
                action=DecisionAction.ENTER,
                target_position_pct=Decimal("0.08000000"),
                sizing_version="position-sizing-v1",
                sizing_input_hash="1" * 64,
            ),
        ),
        input_hash="2" * 64,
    )


def _config(dataset_id: str, dataset_hash: str, start: date, end: date) -> BacktestConfig:
    return BacktestConfig(
        run_id="bt-c-1",
        experiment_arm=ExperimentArm.C_JEV_LLM,
        start_date=start,
        end_date=end,
        warmup_trading_days=20,
        initial_cash=Decimal(1000000),
        benchmark_symbol="000300.SH",
        dataset_id=dataset_id,
        dataset_hash=dataset_hash,
        execution_policy_version="execution-v1",
        sizing_version="position-sizing-v1",
    )


@pytest.mark.asyncio
async def test_store_and_provider_replay_two_days_without_external_calls(
    tmp_path: Path,
) -> None:
    dataset_id = "c-group-202609"
    dataset_hash = "9" * 64
    days = (_day(date(2026, 9, 18), "1"), _day(date(2026, 9, 21), "2"))
    store = FrozenTargetStore(tmp_path)
    hashes: dict[date, str] = {}
    for day in days:
        bundle = _bundle(dataset_id, dataset_hash, day)
        stored_path = store.save(bundle)
        assert stored_path == (
            tmp_path / dataset_id / day.decision_date.isoformat() / f"{bundle.content_hash}.json"
        )
        hashes[day.decision_date] = bundle.content_hash
    provider = FrozenCGroupTargetProvider(
        store=store,
        dataset_id=dataset_id,
        content_hashes=hashes,
    )
    config = _config(dataset_id, dataset_hash, days[0].decision_date, days[1].decision_date)
    portfolio = PortfolioState(
        portfolio_id="paper-main",
        cash_balance=Decimal(1000000),
        frozen_cash=Decimal(0),
        realized_pnl=Decimal(0),
        version=1,
    )

    results = tuple(
        [await provider.build_targets(config, day, portfolio) for day in days]
    )

    assert all(item.experiment_arm is ExperimentArm.C_JEV_LLM for item in results)
    assert tuple(item.decision_date for item in results) == tuple(
        day.decision_date for day in days
    )
    assert all(item.targets[0].symbol == "600000.SH" for item in results)
    source = inspect.getsource(FrozenCGroupTargetProvider).lower()
    assert all(
        forbidden not in source
        for forbidden in (
            "deepseek",
            "jevmarketprovider",
            "httpx",
            "requests",
            "fastapi",
            "sqlalchemy",
            "repository",
        )
    )


@pytest.mark.asyncio
async def test_provider_rejects_day_hash_or_sizing_mismatch(tmp_path: Path) -> None:
    dataset_id = "c-group-202609"
    dataset_hash = "9" * 64
    day = _day(date(2026, 9, 18), "1")
    store = FrozenTargetStore(tmp_path)
    bundle = _bundle(dataset_id, dataset_hash, day)
    store.save(bundle)
    provider = FrozenCGroupTargetProvider(
        store=store,
        dataset_id=dataset_id,
        content_hashes={day.decision_date: bundle.content_hash},
    )
    portfolio = PortfolioState(
        portfolio_id="paper-main",
        cash_balance=Decimal(1000000),
        frozen_cash=Decimal(0),
        realized_pnl=Decimal(0),
        version=1,
    )
    config = _config(dataset_id, dataset_hash, day.decision_date, day.decision_date)

    bad_day = day.model_copy(update={"market_snapshot_hash": "0" * 64})
    with pytest.raises(ValueError, match="FROZEN_TARGET_DAY_MISMATCH"):
        await provider.build_targets(config, bad_day, portfolio)
    bad_config = config.model_copy(update={"sizing_version": "position-sizing-v2"})
    with pytest.raises(ValueError, match="FROZEN_TARGET_SIZING_MISMATCH"):
        await provider.build_targets(bad_config, day, portfolio)


def test_store_rejects_path_escape_and_content_tampering(tmp_path: Path) -> None:
    day = _day(date(2026, 9, 18), "1")
    with pytest.raises(ValueError, match="FROZEN_TARGET_DATASET_ID_INVALID"):
        FrozenTargetStore(tmp_path).save(_bundle("../escape", "9" * 64, day))

    store = FrozenTargetStore(tmp_path)
    bundle = _bundle("safe-dataset", "9" * 64, day)
    path = store.save(bundle)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["targets"][0]["target_position_pct"] = "0.09000000"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="FROZEN_TARGET_CONTENT_HASH_MISMATCH"):
        store.load(bundle.dataset_id, bundle.decision_date, bundle.content_hash)
