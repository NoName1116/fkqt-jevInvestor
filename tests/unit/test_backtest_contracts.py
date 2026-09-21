from fkqt_jevinvestor.domain.backtest import (
    BacktestConfig,
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
