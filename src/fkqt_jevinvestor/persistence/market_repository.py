import hashlib
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.domain.market_features import (
    FeatureValue,
    MarketFeatureSnapshot,
    MarketSnapshot,
    MarketSourceAudit,
)
from fkqt_jevinvestor.domain.market_time import as_utc
from fkqt_jevinvestor.persistence.models import MarketFeatureRecord, MarketSnapshotRecord

SessionFactory = async_sessionmaker[AsyncSession]


class MarketRepositoryError(RuntimeError):
    pass


class MarketSnapshotConflict(MarketRepositoryError):
    pass


class MarketSnapshotNotFound(MarketRepositoryError):
    pass


class StoredMarketSnapshotReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    decision_date: date
    decision_cutoff: datetime
    next_trade_date: date
    calendar_complete_through: date
    universe_snapshot_id: str
    universe_snapshot_hash: str
    content_hash: str
    storage_path: str
    source_manifest_ids: tuple[str, ...]
    source_audits: tuple[MarketSourceAudit, ...]
    created_at: datetime


class StoredMarketRunInputs(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference: StoredMarketSnapshotReference
    features: Mapping[str, MarketFeatureSnapshot]


def _stored_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else as_utc(value)


def _feature_id(snapshot_id: str, symbol: str, value: FeatureValue) -> str:
    identity = f"{snapshot_id}|{symbol}|{value.feature_code}|{value.feature_version}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:40]


def _reference(record: MarketSnapshotRecord) -> StoredMarketSnapshotReference:
    return StoredMarketSnapshotReference(
        snapshot_id=record.id,
        decision_date=record.decision_date,
        decision_cutoff=_stored_utc(record.decision_cutoff),
        next_trade_date=record.next_trade_date,
        calendar_complete_through=record.calendar_complete_through,
        universe_snapshot_id=record.universe_snapshot_id,
        universe_snapshot_hash=record.universe_snapshot_hash,
        content_hash=record.content_hash,
        storage_path=record.storage_path,
        source_manifest_ids=tuple(record.source_manifests),
        source_audits=tuple(MarketSourceAudit.model_validate(item) for item in record.source_audits),
        created_at=_stored_utc(record.created_at),
    )


def _feature_snapshot(
    symbol: str,
    decision_date: date,
    records: list[MarketFeatureRecord],
) -> MarketFeatureSnapshot:
    hashes = {record.feature_snapshot_hash for record in records}
    if len(hashes) != 1:
        raise MarketSnapshotConflict("MARKET_FEATURE_HASH_CONFLICT")
    values = {
        record.feature_code: FeatureValue(
            feature_code=record.feature_code,
            feature_version=record.feature_version,
            as_of=_stored_utc(record.as_of),
            lookback_window=record.lookback_window,
            value=(record.value.quantize(Decimal("0.00000001")) if record.value is not None else None),
            missing_reason=record.missing_reason,
            source_snapshot_hash=record.source_snapshot_hash,
        )
        for record in records
    }
    unhashed = MarketFeatureSnapshot(
        symbol=symbol,
        decision_date=decision_date,
        values=values,
        content_hash="0" * 64,
    )
    return unhashed.model_copy(update={"content_hash": hashes.pop()})


class MarketSnapshotRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def save_run_inputs(
        self,
        snapshot: MarketSnapshot,
        features: Mapping[str, MarketFeatureSnapshot],
        storage_path: str,
    ) -> StoredMarketSnapshotReference:
        if any(symbol != item.symbol for symbol, item in features.items()):
            raise ValueError("feature mapping key must equal feature snapshot symbol")
        if any(item.decision_date != snapshot.decision_date for item in features.values()):
            raise ValueError("feature decision_date must equal market snapshot decision_date")

        try:
            async with self._session_factory.begin() as session:
                existing_by_id = await session.get(MarketSnapshotRecord, snapshot.snapshot_id)
                if existing_by_id is not None:
                    if existing_by_id.content_hash != snapshot.content_hash:
                        raise MarketSnapshotConflict("MARKET_SNAPSHOT_CONFLICT")
                    return _reference(existing_by_id)

                existing_by_content = await session.scalar(
                    select(MarketSnapshotRecord).where(
                        MarketSnapshotRecord.decision_date == snapshot.decision_date,
                        MarketSnapshotRecord.content_hash == snapshot.content_hash,
                    )
                )
                if existing_by_content is not None:
                    return _reference(existing_by_content)

                now = datetime.now(UTC)
                record = MarketSnapshotRecord(
                    id=snapshot.snapshot_id,
                    decision_date=snapshot.decision_date,
                    decision_cutoff=as_utc(snapshot.decision_cutoff),
                    next_trade_date=snapshot.next_trade_date,
                    calendar_complete_through=snapshot.calendar_complete_through,
                    universe_snapshot_id=snapshot.universe_snapshot_id,
                    universe_snapshot_hash=snapshot.universe_snapshot_hash,
                    content_hash=snapshot.content_hash,
                    storage_path=storage_path,
                    source_manifests=list(snapshot.source_manifest_ids),
                    source_audits=[
                        audit.model_dump(mode="json") for audit in snapshot.source_audits
                    ],
                    created_at=now,
                )
                session.add(record)
                for symbol in sorted(features):
                    for value in features[symbol].values.values():
                        session.add(
                            MarketFeatureRecord(
                                id=_feature_id(snapshot.snapshot_id, symbol, value),
                                market_snapshot_id=snapshot.snapshot_id,
                                symbol=symbol,
                                feature_code=value.feature_code,
                                feature_version=value.feature_version,
                                as_of=as_utc(value.as_of),
                                lookback_window=value.lookback_window,
                                value=value.value,
                                missing_reason=value.missing_reason,
                                source_snapshot_hash=value.source_snapshot_hash,
                                feature_snapshot_hash=features[symbol].content_hash,
                            )
                        )
                await session.flush()
                return _reference(record)
        except MarketRepositoryError:
            raise
        except IntegrityError as exc:
            raise MarketSnapshotConflict("MARKET_SNAPSHOT_CONFLICT") from exc

    async def load_run_inputs(
        self,
        decision_date: date,
        content_hash: str,
    ) -> StoredMarketRunInputs:
        async with self._session_factory() as session:
            snapshot = await session.scalar(
                select(MarketSnapshotRecord).where(
                    MarketSnapshotRecord.decision_date == decision_date,
                    MarketSnapshotRecord.content_hash == content_hash,
                )
            )
            if snapshot is None:
                raise MarketSnapshotNotFound("MARKET_SNAPSHOT_NOT_FOUND")
            return await self._load_record(session, snapshot)

    async def load_run_inputs_by_id(self, snapshot_id: str) -> StoredMarketRunInputs:
        async with self._session_factory() as session:
            snapshot = await session.get(MarketSnapshotRecord, snapshot_id)
            if snapshot is None:
                raise MarketSnapshotNotFound("MARKET_SNAPSHOT_NOT_FOUND")
            return await self._load_record(session, snapshot)

    @staticmethod
    async def _load_record(
        session: AsyncSession,
        snapshot: MarketSnapshotRecord,
    ) -> StoredMarketRunInputs:
            records = tuple(
                await session.scalars(
                    select(MarketFeatureRecord)
                    .where(MarketFeatureRecord.market_snapshot_id == snapshot.id)
                    .order_by(MarketFeatureRecord.symbol, MarketFeatureRecord.feature_code)
                )
            )
            grouped: defaultdict[str, list[MarketFeatureRecord]] = defaultdict(list)
            for record in records:
                grouped[record.symbol].append(record)
            features = {
                symbol: _feature_snapshot(symbol, snapshot.decision_date, feature_records)
                for symbol, feature_records in sorted(grouped.items())
            }
            return StoredMarketRunInputs(reference=_reference(snapshot), features=features)
