"""A/B/C/D 四组回测运行器。"""

from collections.abc import Callable, Mapping

from fkqt_jevinvestor.backtest.engine import BacktestEngine, BacktestError
from fkqt_jevinvestor.domain.backtest import (
    BacktestConfig,
    BacktestResult,
    ExecutionPort,
    ExperimentArm,
    ReplayDataProvider,
    TargetProvider,
)


def _fields_match(a: BacktestConfig, b: BacktestConfig) -> bool:
    """九个共享字段完全一致才返回 True；run_id 与 experiment_arm 允许不同。"""
    return (
        a.start_date == b.start_date
        and a.end_date == b.end_date
        and a.warmup_trading_days == b.warmup_trading_days
        and a.initial_cash == b.initial_cash
        and a.benchmark_symbol == b.benchmark_symbol
        and a.dataset_id == b.dataset_id
        and a.dataset_hash == b.dataset_hash
        and a.execution_policy_version == b.execution_policy_version
        and a.sizing_version == b.sizing_version
    )


def _validate_arms(
    configs: tuple[BacktestConfig, ...],
    target_providers: Mapping[ExperimentArm, TargetProvider],
) -> None:
    """整批检查：先查目标生成器是否齐全，再查九个共享字段是否一致。"""
    if not configs:
        return
    if len({config.experiment_arm for config in configs}) != len(configs):
        raise BacktestError("EXPERIMENT_CONFIG_MISMATCH: DUPLICATE_ARM")

    for config in configs:
        if config.experiment_arm not in target_providers:
            # 契约没有为公平性失败定义专属错误码，因此用消息前缀 EXPERIMENT_CONFIG_MISMATCH 表达；该口径以运行手册为准。
            raise BacktestError(
                f"EXPERIMENT_CONFIG_MISMATCH: 缺少 {config.experiment_arm.value} 组的目标生成器"
            )

    reference = configs[0]
    for config in configs[1:]:
        if not _fields_match(reference, config):
            raise BacktestError("EXPERIMENT_CONFIG_MISMATCH: 四组共享字段不一致")


async def run_experiment_arms(
    configs: tuple[BacktestConfig, ...],
    replay: ReplayDataProvider,
    target_providers: Mapping[ExperimentArm, TargetProvider],
    execution_factory: Callable[[], ExecutionPort],
) -> Mapping[ExperimentArm, BacktestResult]:
    """先整批检查，再逐组创建独立执行器并运行。"""
    _validate_arms(configs, target_providers)

    results: dict[ExperimentArm, BacktestResult] = {}
    for config in configs:
        port = execution_factory()
        engine = BacktestEngine(
            replay=replay,
            targets=target_providers[config.experiment_arm],
            execution=port,
        )
        results[config.experiment_arm] = await engine.run(config)
    return results
