import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.backtest import ReplayDay
from fkqt_jevinvestor.domain.candidate_queue import CandidateQueueV1
from fkqt_jevinvestor.domain.execution import ExecutionPolicy
from fkqt_jevinvestor.domain.market import TradingStatus
from fkqt_jevinvestor.domain.market_features import MarketFeatureSnapshot, MarketSnapshot
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.ingestion.execution_bundle import ExecutionBundleV1
from fkqt_jevinvestor.ingestion.execution_bundle_v2 import ExecutionBundleV2
from fkqt_jevinvestor.ingestion.snapshot_store import MarketSnapshotStore
from fkqt_jevinvestor.research.artifacts import write_immutable


class ResearchDayV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot: MarketSnapshot
    features: Mapping[str, MarketFeatureSnapshot]
    queue: CandidateQueueV1
    execution: ExecutionBundleV1 | ExecutionBundleV2

    @property
    def content_hash(self) -> str:
        return sha256_json(self)

    @property
    def feature_hash(self) -> str:
        return sha256_json(
            {symbol: item.content_hash for symbol, item in sorted(self.features.items())}
        )

    def verify(self) -> None:
        MarketSnapshotStore(Path()).validate(self.snapshot)
        self.queue.verify()
        snapshot = self.snapshot
        if self.queue.trade_date != snapshot.decision_date:
            raise ValueError("RESEARCH_QUEUE_DATE_MISMATCH")
        if self.queue.source_id != snapshot.universe_snapshot_hash:
            raise ValueError("RESEARCH_QUEUE_SOURCE_MISMATCH")
        if not set(self.queue.ordered_symbols).issubset(self.features):
            raise ValueError("RESEARCH_FEATURE_COVERAGE_INCOMPLETE")
        if any(
            symbol not in snapshot.security_states or not snapshot.daily_bars.get(symbol)
            for symbol in self.features
        ):
            raise ValueError("RESEARCH_SECURITY_COVERAGE_INCOMPLETE")
        for symbol, feature in self.features.items():
            if symbol != feature.symbol or feature.decision_date != snapshot.decision_date:
                raise ValueError("RESEARCH_FEATURE_IDENTITY_MISMATCH")
            for code, value in feature.values.items():
                if value.as_of > snapshot.decision_cutoff:
                    raise ValueError("RESEARCH_POINT_IN_TIME_VIOLATION")
                if (
                    code != value.feature_code
                    or value.source_snapshot_hash != snapshot.content_hash
                ):
                    raise ValueError("RESEARCH_FEATURE_SOURCE_MISMATCH")
            if (
                sha256_json(feature.model_dump(mode="json") | {"content_hash": ""})
                != feature.content_hash
            ):
                raise ValueError("RESEARCH_FEATURE_HASH_MISMATCH")
        self.execution.verify(snapshot.next_trade_date, set(self.features))
        if isinstance(self.execution, ExecutionBundleV2):
            if self.execution.calendar_proof.previous_trade_date != snapshot.decision_date:
                raise ValueError("RESEARCH_CALENDAR_CHAIN_MISMATCH")
            if (
                self.execution.corporate_actions is not None
                and self.execution.corporate_actions.events
            ):
                raise ValueError("LEGACY_BACKTEST_CORPORATE_ACTION_UNSUPPORTED")
        if any(
            state.corporate_action_status != "NONE" for state in snapshot.security_states.values()
        ):
            raise ValueError("LEGACY_BACKTEST_CORPORATE_ACTION_UNSUPPORTED")
        for symbol, state in snapshot.security_states.items():
            if symbol != state.symbol:
                raise ValueError("RESEARCH_SECURITY_IDENTITY_MISMATCH")
        for value in self.execution.snapshots.values():
            if value.trading_status is TradingStatus.TRADING and (
                value.open_price is None
                or value.daily_amount_cny is None
                or (
                    not value.is_initial_no_limit_period
                    and (value.upper_limit_price is None or value.lower_limit_price is None)
                )
            ):
                raise ValueError("RESEARCH_EXECUTION_DATA_INCOMPLETE")

    def replay_day(self) -> ReplayDay:
        self.verify()
        return ReplayDay(
            decision_date=self.snapshot.decision_date,
            decision_cutoff=self.snapshot.decision_cutoff,
            planned_execution_date=self.snapshot.next_trade_date,
            universe_snapshot_hash=self.snapshot.universe_snapshot_hash,
            market_snapshot_hash=self.snapshot.content_hash,
            feature_snapshot_hash=self.feature_hash,
            features=self.features,
            execution_market=self.execution.snapshots,
        )


class DayReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    decision_date: date
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DatasetManifestV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["research-dataset-v1"] = "research-dataset-v1"
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    days: tuple[DayReference, ...]
    execution_policy: ExecutionPolicy = ExecutionPolicy()

    @property
    def dataset_hash(self) -> str:
        return sha256_json(self)


class DatasetStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def publish(self, dataset_id: str, days: tuple[ResearchDayV1, ...]) -> Path:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", dataset_id) is None:
            raise ValueError("RESEARCH_DATASET_ID_INVALID")
        self.verify_days(days)
        manifest = DatasetManifestV1(
            dataset_id=dataset_id,
            days=tuple(
                DayReference(
                    decision_date=item.snapshot.decision_date, content_hash=item.content_hash
                )
                for item in days
            ),
        )
        directory = self.root / dataset_id / manifest.dataset_hash
        if self.root not in directory.resolve().parents:
            raise ValueError("RESEARCH_PATH_ESCAPE")
        for item in days:
            write_immutable(directory / "days" / f"{item.content_hash}.json", item)
        return write_immutable(directory / "manifest.json", manifest)

    def load(self, path: Path) -> tuple[DatasetManifestV1, tuple[ResearchDayV1, ...]]:
        path = path.resolve()
        if self.root not in path.parents:
            raise ValueError("RESEARCH_PATH_ESCAPE")
        manifest = DatasetManifestV1.model_validate_json(path.read_text(encoding="utf-8"))
        if (
            path.parent.name != manifest.dataset_hash
            or path.parent.parent.name != manifest.dataset_id
        ):
            raise ValueError("RESEARCH_MANIFEST_HASH_MISMATCH")
        days: list[ResearchDayV1] = []
        for ref in manifest.days:
            chunk = (path.parent / "days" / f"{ref.content_hash}.json").resolve()
            if path.parent not in chunk.parents:
                raise ValueError("RESEARCH_PATH_ESCAPE")
            day = ResearchDayV1.model_validate_json(chunk.read_text(encoding="utf-8"))
            if (
                day.content_hash != ref.content_hash
                or day.snapshot.decision_date != ref.decision_date
            ):
                raise ValueError("RESEARCH_DAY_HASH_MISMATCH")
            days.append(day)
        self.verify_days(tuple(days))
        return manifest, tuple(days)

    @staticmethod
    def verify_days(days: tuple[ResearchDayV1, ...]) -> None:
        if not days:
            raise ValueError("RESEARCH_DATASET_EMPTY")
        prior: ResearchDayV1 | None = None
        potential_holdings: set[str] = set()
        for day in days:
            day.verify()
            if prior is not None and prior.snapshot.next_trade_date != day.snapshot.decision_date:
                raise ValueError("RESEARCH_CALENDAR_CHAIN_MISMATCH")
            potential_holdings.update(day.queue.ordered_symbols)
            if not potential_holdings.issubset(day.features) or not potential_holdings.issubset(
                day.execution.snapshots
            ):
                raise ValueError("RESEARCH_HELD_SYMBOL_COVERAGE_INCOMPLETE")
            prior = day


class FrozenReplayDataProvider:
    def __init__(self, store: DatasetStore, manifest_path: Path) -> None:
        self.store = store
        self.manifest_path = manifest_path
        self.manifest, _ = store.load(manifest_path)

    def research_day(self, decision_date: date) -> ResearchDayV1:
        manifest, days = self.store.load(self.manifest_path)
        if manifest != self.manifest:
            raise ValueError("RESEARCH_MANIFEST_CHANGED")
        for day in days:
            if day.snapshot.decision_date == decision_date:
                return day
        raise ValueError("RESEARCH_DATE_NOT_FOUND")

    async def decision_dates(self, start: date, end: date) -> tuple[date, ...]:
        if end < start:
            raise ValueError("RESEARCH_DATE_RANGE_INVALID")
        return tuple(
            ref.decision_date for ref in self.manifest.days if start <= ref.decision_date <= end
        )

    def candidate_symbols(self, decision_date: date) -> tuple[str, ...]:
        return self.research_day(decision_date).queue.ordered_symbols

    async def warmup_dates(self, before: date, count: int) -> tuple[date, ...]:
        if count < 0:
            raise ValueError("RESEARCH_WARMUP_INVALID")
        prior = tuple(ref.decision_date for ref in self.manifest.days if ref.decision_date < before)
        if len(prior) < count:
            raise ValueError("RESEARCH_WARMUP_INCOMPLETE")
        return prior[-count:] if count else ()

    async def load_day(self, decision_date: date) -> ReplayDay:
        return self.research_day(decision_date).replay_day()
