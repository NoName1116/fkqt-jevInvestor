from collections.abc import Mapping
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.backtest import (
    BacktestConfig,
    DecisionReplayDay,
    ExperimentArm,
    TargetPositionBatch,
)
from fkqt_jevinvestor.domain.portfolio import PortfolioState
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.research.artifacts import write_immutable


def reject_legacy_rights(portfolio: PortfolioState) -> None:
    if (
        portfolio.dividend_receivable
        or portfolio.dividend_income
        or any(p.locked_bonus_quantity for p in portfolio.positions)
    ):
        raise ValueError("LEGACY_BACKTEST_CORPORATE_ACTION_UNSUPPORTED")


class RecordedTargetV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    day: DecisionReplayDay
    portfolio: PortfolioState
    targets: TargetPositionBatch

    @property
    def content_hash(self) -> str:
        return sha256_json(self)

    def save(self, root: Path) -> Path:
        reject_legacy_rights(self.portfolio)
        return write_immutable(root / f"{self.content_hash}.json", self)


class FrozenRecordedTargetProvider:
    def __init__(self, paths: Mapping[date, Path]) -> None:
        self.paths = dict(paths)

    async def build_targets(
        self, config: BacktestConfig, day: DecisionReplayDay, portfolio: PortfolioState
    ) -> TargetPositionBatch:
        reject_legacy_rights(portfolio)
        path = self.paths.get(day.decision_date)
        if path is None:
            raise ValueError("RECORDED_TARGET_DATE_NOT_FOUND")
        record = RecordedTargetV1.model_validate_json(path.read_text(encoding="utf-8"))
        if path.stem != record.content_hash:
            raise ValueError("RECORDED_TARGET_HASH_MISMATCH")
        if (
            config.experiment_arm is not ExperimentArm.C_JEV_LLM
            or record.dataset_id != config.dataset_id
            or record.dataset_hash != config.dataset_hash
            or record.targets.experiment_arm is not config.experiment_arm
            or any(
                target.sizing_version != config.sizing_version for target in record.targets.targets
            )
        ):
            raise ValueError("RECORDED_TARGET_CONFIG_MISMATCH")
        if record.day != day or (
            record.targets.decision_date != day.decision_date
            or record.targets.planned_execution_date != day.planned_execution_date
        ):
            raise ValueError("RECORDED_TARGET_DAY_MISMATCH")
        if record.portfolio != portfolio:
            raise ValueError("RECORDED_TARGET_PORTFOLIO_MISMATCH")
        return record.targets
