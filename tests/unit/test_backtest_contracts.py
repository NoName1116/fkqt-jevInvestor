from datetime import UTC, date, datetime
from typing import get_type_hints

from fkqt_jevinvestor.domain.backtest import (
    BacktestConfig,
    DecisionReplayDay,
    ExecutionPort,
    ExperimentArm,
    ReplayDataProvider,
    ReplayDay,
    TargetPositionBatch,
    TargetProvider,
)
from fkqt_jevinvestor.services.frozen_c_group_target import (
    FrozenCGroupTargetProvider,
)


def test_experiment_arm_has_canonical_names_without_removing_legacy_names() -> None:
    assert ExperimentArm.A_RULE.value == "A_RULE"
    assert ExperimentArm.B_LLM.value == "B_LLM"
    assert ExperimentArm.C_JEV_LLM.value == "C_JEV_LLM"
    assert ExperimentArm.D_JEV_DIRECT.value == "D_JEV_DIRECT"
    assert ExperimentArm.A_LLM.value == "A_LLM"
    assert ExperimentArm.B_JEV_LLM.value == "B_JEV_LLM"
    assert ExperimentArm.C_JEV_DIRECT.value == "C_JEV_DIRECT"
    assert ExperimentArm.D_RULE.value == "D_RULE"


def test_backtest_contract_v1_public_names_are_importable() -> None:
    assert BacktestConfig
    assert ReplayDay
    assert TargetPositionBatch
    assert ReplayDataProvider
    assert TargetProvider
    assert ExecutionPort


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
    assert (
        get_type_hints(FrozenCGroupTargetProvider.build_targets)["day"]
        is DecisionReplayDay
    )
