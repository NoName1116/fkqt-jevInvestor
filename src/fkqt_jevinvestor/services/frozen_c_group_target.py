import os
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fkqt_jevinvestor.domain.backtest import (
    BacktestConfig,
    DecisionReplayDay,
    ExperimentArm,
    TargetPosition,
    TargetPositionBatch,
)
from fkqt_jevinvestor.domain.portfolio import PortfolioState
from fkqt_jevinvestor.ingestion.canonical import canonical_json, sha256_json

_SAFE_DATASET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FrozenTargetBundleV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_id: str = Field(min_length=1, max_length=128)
    dataset_hash: str = Field(min_length=64, max_length=64)
    decision_date: date
    planned_execution_date: date
    universe_snapshot_hash: str = Field(min_length=64, max_length=64)
    market_snapshot_hash: str = Field(min_length=64, max_length=64)
    feature_snapshot_hash: str = Field(min_length=64, max_length=64)
    sizing_version: str = Field(min_length=1)
    targets: tuple[TargetPosition, ...]
    input_hash: str = Field(min_length=64, max_length=64)
    content_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_identity(self) -> "FrozenTargetBundleV1":
        if self.planned_execution_date <= self.decision_date:
            raise ValueError("FROZEN_TARGET_EXECUTION_DATE_INVALID")
        if len({target.symbol for target in self.targets}) != len(self.targets):
            raise ValueError("FROZEN_TARGET_DUPLICATE_SYMBOL")
        if tuple(sorted(self.targets, key=lambda item: item.symbol)) != self.targets:
            raise ValueError("FROZEN_TARGET_ORDER_INVALID")
        if any(target.sizing_version != self.sizing_version for target in self.targets):
            raise ValueError("FROZEN_TARGET_SIZING_MISMATCH")
        return self

    @classmethod
    def create(
        cls,
        *,
        dataset_id: str,
        dataset_hash: str,
        decision_date: date,
        planned_execution_date: date,
        universe_snapshot_hash: str,
        market_snapshot_hash: str,
        feature_snapshot_hash: str,
        sizing_version: str,
        targets: tuple[TargetPosition, ...],
        input_hash: str,
    ) -> "FrozenTargetBundleV1":
        ordered = tuple(sorted(targets, key=lambda item: item.symbol))
        values = {
            "dataset_id": dataset_id,
            "dataset_hash": dataset_hash,
            "decision_date": decision_date,
            "planned_execution_date": planned_execution_date,
            "universe_snapshot_hash": universe_snapshot_hash,
            "market_snapshot_hash": market_snapshot_hash,
            "feature_snapshot_hash": feature_snapshot_hash,
            "sizing_version": sizing_version,
            "targets": ordered,
            "input_hash": input_hash,
        }
        provisional = cls(**values, content_hash="0" * 64)
        return cls(
            **values,
            content_hash=sha256_json(
                provisional.model_dump(mode="json", exclude={"content_hash"})
            ),
        )

    def computed_content_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json", exclude={"content_hash"}))


class FrozenTargetStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def save(self, bundle: FrozenTargetBundleV1) -> Path:
        self._validate_dataset_id(bundle.dataset_id)
        self._validate_hash(bundle.content_hash)
        if bundle.computed_content_hash() != bundle.content_hash:
            raise ValueError("FROZEN_TARGET_CONTENT_HASH_MISMATCH")
        path = self._path(bundle.dataset_id, bundle.decision_date, bundle.content_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(canonical_json(bundle), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return path

    def load(
        self,
        dataset_id: str,
        decision_date: date,
        content_hash: str,
    ) -> FrozenTargetBundleV1:
        path = self._path(dataset_id, decision_date, content_hash)
        try:
            bundle = FrozenTargetBundleV1.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except FileNotFoundError as exc:
            raise ValueError("FROZEN_TARGET_NOT_FOUND") from exc
        if (
            bundle.dataset_id != dataset_id
            or bundle.decision_date != decision_date
            or bundle.content_hash != content_hash
            or bundle.computed_content_hash() != content_hash
        ):
            raise ValueError("FROZEN_TARGET_CONTENT_HASH_MISMATCH")
        return bundle

    def _path(self, dataset_id: str, decision_date: date, content_hash: str) -> Path:
        self._validate_dataset_id(dataset_id)
        self._validate_hash(content_hash)
        path = (
            self._root
            / dataset_id
            / decision_date.isoformat()
            / f"{content_hash}.json"
        ).resolve()
        if self._root not in path.parents:
            raise ValueError("FROZEN_TARGET_PATH_ESCAPE")
        return path

    @staticmethod
    def _validate_dataset_id(dataset_id: str) -> None:
        if _SAFE_DATASET_ID.fullmatch(dataset_id) is None:
            raise ValueError("FROZEN_TARGET_DATASET_ID_INVALID")

    @staticmethod
    def _validate_hash(content_hash: str) -> None:
        if _SHA256.fullmatch(content_hash) is None:
            raise ValueError("FROZEN_TARGET_CONTENT_HASH_INVALID")


class FrozenCGroupTargetProvider:
    def __init__(
        self,
        *,
        store: FrozenTargetStore,
        dataset_id: str,
        content_hashes: Mapping[date, str],
    ) -> None:
        self._store = store
        self._dataset_id = dataset_id
        self._content_hashes = dict(content_hashes)

    async def build_targets(
        self,
        config: BacktestConfig,
        day: DecisionReplayDay,
        portfolio: PortfolioState,
    ) -> TargetPositionBatch:
        del portfolio
        if (
            config.experiment_arm is not ExperimentArm.C_JEV_LLM
            or config.dataset_id != self._dataset_id
        ):
            raise ValueError("FROZEN_TARGET_CONFIG_MISMATCH")
        content_hash = self._content_hashes.get(day.decision_date)
        if content_hash is None:
            raise ValueError("FROZEN_TARGET_DATE_NOT_FOUND")
        bundle = self._store.load(self._dataset_id, day.decision_date, content_hash)
        if bundle.dataset_hash != config.dataset_hash:
            raise ValueError("FROZEN_TARGET_DATASET_HASH_MISMATCH")
        if bundle.sizing_version != config.sizing_version:
            raise ValueError("FROZEN_TARGET_SIZING_MISMATCH")
        if (
            bundle.decision_date != day.decision_date
            or bundle.planned_execution_date != day.planned_execution_date
            or bundle.universe_snapshot_hash != day.universe_snapshot_hash
            or bundle.market_snapshot_hash != day.market_snapshot_hash
            or bundle.feature_snapshot_hash != day.feature_snapshot_hash
        ):
            raise ValueError("FROZEN_TARGET_DAY_MISMATCH")
        return TargetPositionBatch(
            decision_date=bundle.decision_date,
            planned_execution_date=bundle.planned_execution_date,
            experiment_arm=ExperimentArm.C_JEV_LLM,
            targets=bundle.targets,
            input_hash=bundle.input_hash,
        )
