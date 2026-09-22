from datetime import UTC, date, datetime
from typing import get_type_hints

from fkqt_jevinvestor.domain.backtest import (
    BacktestConfig,
    BacktestExecutionResult,
    DecisionReplayDay,
    ExecutionPort,
    ReplayDataProvider,
    ReplayDay,
    TargetPositionBatch,
    TargetProvider,
)


def test_backtest_contract_v1_public_names_are_importable() -> None:
    assert BacktestConfig
    assert ReplayDay
    assert TargetPositionBatch
    assert ReplayDataProvider
    assert TargetProvider
    assert ExecutionPort


def test_replay_provider_exposes_explicit_warmup_dates() -> None:
    hints = get_type_hints(ReplayDataProvider.warmup_dates)

    assert hints["before"] is date
    assert hints["count"] is int
    assert hints["return"] == tuple[date, ...]


def test_execution_result_allows_zero_equity_for_stable_engine_error() -> None:
    assert BacktestExecutionResult.model_fields["total_equity"].metadata[0].ge == 0


def test_target_provider_decision_view_cannot_access_d_plus_one_market() -> None:
    replay = ReplayDay(
        decision_date=date(2026, 9, 18),
        decision_cutoff=datetime(2026, 9, 18, 15, tzinfo=UTC),
        planned_execution_date=date(2026, 9, 21),
        universe_snapshot_hash="a" * 64,
        market_snapshot_hash="b" * 64,
        feature_snapshot_hash="c" * 64,
        features={},
        execution_market={},
    )

    decision_view = replay.decision_view()

    assert isinstance(decision_view, DecisionReplayDay)
    assert not hasattr(decision_view, "execution_market")
    assert get_type_hints(TargetProvider.build_targets)["day"] is DecisionReplayDay
