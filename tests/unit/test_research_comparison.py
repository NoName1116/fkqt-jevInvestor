from decimal import Decimal
from pathlib import Path

import pytest

from fkqt_jevinvestor.domain.backtest import (
    BacktestResult,
    BacktestSummary,
    DailyBacktestRecord,
    ExperimentArm,
)
from tests.unit.research_fixtures import config


def result(arm: ExperimentArm, value: str) -> BacktestResult:
    cfg = config(arm, "a" * 64)
    record = DailyBacktestRecord(
        decision_date=cfg.start_date,
        execution_date=cfg.start_date.replace(day=21),
        total_equity=Decimal(100000),
        cash_balance=Decimal(100000),
        market_value=Decimal(0),
        daily_return=Decimal(0),
        cumulative_return=Decimal(value),
        drawdown=Decimal("0.01"),
        turnover=Decimal(0),
        fees=Decimal(0),
        submitted_orders=0,
        filled_orders=0,
        rejected_orders=0,
    )
    summary = BacktestSummary(
        run_id=cfg.run_id,
        experiment_arm=arm,
        trading_days=1,
        cumulative_return=Decimal(value),
        annualized_return=Decimal(value),
        max_drawdown=Decimal("0.01"),
        sharpe_ratio=None,
        sortino_ratio=None,
        turnover=Decimal(0),
        total_fees=Decimal(0),
        submitted_orders=0,
        filled_orders=0,
        rejected_orders=0,
        fill_rate=None,
        config_hash="b" * 64,
        result_hash="c" * 64,
    )
    return BacktestResult(config=cfg, daily_records=(record,), summary=summary)


def test_comparison_requires_all_groups_and_same_dataset() -> None:
    from fkqt_jevinvestor.research.comparison import compare_results

    results = tuple(
        result(a, v)
        for a, v in zip(
            (
                ExperimentArm.A_RULE,
                ExperimentArm.B_LLM,
                ExperimentArm.C_JEV_LLM,
                ExperimentArm.D_JEV_DIRECT,
            ),
            ("0.01", "0.02", "0.03", "0.04"),
            strict=True,
        )
    )
    comparison = compare_results(results)
    assert comparison.verification_status == "DECLARED_CONFIG_ONLY"
    assert comparison.differences["C_JEV_LLM_minus_B_LLM"]["cumulative_return"] == Decimal("0.01")
    assert comparison.differences["C_JEV_LLM_minus_A_RULE"]["max_drawdown"] == 0
    with pytest.raises(ValueError, match="GROUPS_INCOMPLETE"):
        compare_results(results[:-1])
    changed = results[-1].model_copy(
        update={"config": results[-1].config.model_copy(update={"dataset_hash": "d" * 64})}
    )
    with pytest.raises(ValueError, match="CONFIG_MISMATCH"):
        compare_results((*results[:-1], changed))


async def test_comparison_verifies_actual_sizing_and_daily_audit(tmp_path: Path) -> None:
    from fkqt_jevinvestor.research.comparison import compare_results
    from fkqt_jevinvestor.research.models import ResearchStrategyConfig
    from fkqt_jevinvestor.research.strategy import ExperimentTargetProvider
    from tests.unit.research_fixtures import cash
    from tests.unit.test_research_strategy import FakeJev, FakeLlm, provider

    arms = (
        ExperimentArm.A_RULE,
        ExperimentArm.B_LLM,
        ExperimentArm.C_JEV_LLM,
        ExperimentArm.D_JEV_DIRECT,
    )
    results: list[BacktestResult] = []
    _, replay, _ = provider(tmp_path, ExperimentArm.A_RULE)
    day = (
        await replay.load_day(config(ExperimentArm.A_RULE, replay.manifest.dataset_hash).start_date)
    ).decision_view()
    for arm in arms:
        strategy, replay, cfg = provider(tmp_path, arm, llm=FakeLlm(), jev=FakeJev())
        day = (await replay.load_day(cfg.start_date)).decision_view()
        await strategy.build_targets(cfg, day, cash())
        results.append(result(arm, "0.01").model_copy(update={"config": cfg}))
    report = compare_results(tuple(results), audit_root=tmp_path / "audit")
    assert report.verification_status == "AUDIT_VERIFIED"
    assert len(report.audit_hashes) == 4
    with pytest.raises(ValueError, match="AUDIT_INCOMPLETE"):
        compare_results(tuple(results), audit_root=tmp_path / "missing")
    changed = ResearchStrategyConfig(
        sizing=ResearchStrategyConfig().sizing.model_copy(
            update={"daily_risk_budget": Decimal("0.004")}
        )
    )
    await ExperimentTargetProvider(
        replay, tmp_path / "changed", changed, llm=FakeLlm()
    ).build_targets(results[1].config, day, cash())
    original = next(
        p
        for p in (tmp_path / "audit" / "experiments").glob("*.json")
        if '"B_LLM"' in p.read_text(encoding="utf-8")
    )
    original.unlink()
    replacement = next((tmp_path / "changed" / "experiments").glob("*.json"))
    (tmp_path / "audit" / "experiments" / replacement.name).write_bytes(replacement.read_bytes())
    with pytest.raises(ValueError, match="STRATEGY_MISMATCH"):
        compare_results(tuple(results), audit_root=tmp_path / "audit")
