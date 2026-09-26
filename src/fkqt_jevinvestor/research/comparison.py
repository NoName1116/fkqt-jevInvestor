from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.backtest import BacktestResult, ExperimentArm
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.research.audit import ExperimentArtifact, verify
from fkqt_jevinvestor.research.models import ResearchStrategyConfig


class ComparisonReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dataset_hash: str
    result_hashes: dict[str, str]
    differences: dict[str, dict[str, Decimal]]
    verification_status: Literal["DECLARED_CONFIG_ONLY", "AUDIT_VERIFIED"]
    audit_hashes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    strategy_hash: str | None = None
    sizing_config_hash: str | None = None
    interpretation: str = "差值不构成统计显著性、样本外增益或未来收益保证。"


def compare_results(
    results: tuple[BacktestResult, ...], *, audit_root: Path | None = None
) -> ComparisonReport:
    arms = {
        ExperimentArm.A_RULE,
        ExperimentArm.B_LLM,
        ExperimentArm.C_JEV_LLM,
        ExperimentArm.D_JEV_DIRECT,
    }
    if len(results) != 4 or {r.config.experiment_arm for r in results} != arms:
        raise ValueError("RESEARCH_COMPARISON_GROUPS_INCOMPLETE")
    first = results[0]
    reference = first.config.model_dump(mode="json", exclude={"run_id", "experiment_arm"})
    dates = tuple((r.decision_date, r.execution_date) for r in first.daily_records)
    if not dates:
        raise ValueError("RESEARCH_COMPARISON_NO_RECORDS")
    for result in results:
        if result.config.model_dump(mode="json", exclude={"run_id", "experiment_arm"}) != reference:
            raise ValueError("RESEARCH_COMPARISON_CONFIG_MISMATCH")
        if (
            result.summary.experiment_arm != result.config.experiment_arm
            or result.summary.run_id != result.config.run_id
            or result.summary.trading_days != len(result.daily_records)
            or tuple((r.decision_date, r.execution_date) for r in result.daily_records) != dates
        ):
            raise ValueError("RESEARCH_COMPARISON_RECORD_MISMATCH")
    audit_hashes: dict[str, tuple[str, ...]] = {}
    strategy_config: dict[str, Any] | None = None
    if audit_root is not None:
        artifacts: list[ExperimentArtifact] = []
        for path in sorted((audit_root / "experiments").glob("*.json")):
            artifact = ExperimentArtifact.model_validate_json(path.read_text(encoding="utf-8"))
            verify(artifact)
            if path.stem != sha256_json(artifact.identity):
                raise ValueError("RESEARCH_COMPARISON_AUDIT_HASH_MISMATCH")
            artifacts.append(artifact)
        for result in results:
            references: list[str] = []
            for record in result.daily_records:
                matches = [
                    artifact
                    for artifact in artifacts
                    if artifact.identity["config"] == result.config.model_dump(mode="json")
                    and artifact.targets.decision_date == record.decision_date
                    and artifact.targets.planned_execution_date == record.execution_date
                    and artifact.targets.experiment_arm == result.config.experiment_arm
                ]
                if len(matches) != 1:
                    raise ValueError("RESEARCH_COMPARISON_AUDIT_INCOMPLETE_OR_AMBIGUOUS")
                strategy = ResearchStrategyConfig.model_validate(matches[0].identity["strategy"])
                actual = strategy.model_dump(mode="json")
                if strategy_config is not None and actual != strategy_config:
                    raise ValueError("RESEARCH_COMPARISON_STRATEGY_MISMATCH")
                strategy_config = actual
                references.append(matches[0].content_hash)
            audit_hashes[result.config.experiment_arm.value] = tuple(references)
    indexed = {r.config.experiment_arm: r for r in results}
    differences: dict[str, dict[str, Decimal]] = {}
    for arm, baseline in (
        (ExperimentArm.B_LLM, ExperimentArm.A_RULE),
        (ExperimentArm.C_JEV_LLM, ExperimentArm.A_RULE),
        (ExperimentArm.C_JEV_LLM, ExperimentArm.B_LLM),
        (ExperimentArm.D_JEV_DIRECT, ExperimentArm.A_RULE),
    ):
        target, base = indexed[arm].summary, indexed[baseline].summary
        differences[f"{arm.value}_minus_{baseline.value}"] = {
            "cumulative_return": target.cumulative_return - base.cumulative_return,
            "max_drawdown": target.max_drawdown - base.max_drawdown,
            "turnover": target.turnover - base.turnover,
            "total_fees": target.total_fees - base.total_fees,
        }
    return ComparisonReport(
        dataset_hash=first.config.dataset_hash,
        result_hashes={r.config.experiment_arm.value: r.summary.result_hash for r in results},
        differences=differences,
        verification_status="AUDIT_VERIFIED" if audit_root is not None else "DECLARED_CONFIG_ONLY",
        audit_hashes=audit_hashes,
        strategy_hash=sha256_json(strategy_config) if strategy_config is not None else None,
        sizing_config_hash=sha256_json(strategy_config["sizing"])
        if strategy_config is not None
        else None,
    )
