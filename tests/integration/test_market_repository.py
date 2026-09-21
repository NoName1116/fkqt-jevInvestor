import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.domain.market_features import (
    FeatureValue,
    MarketFeatureSnapshot,
    MarketSnapshot,
    MarketSourceAudit,
)
from fkqt_jevinvestor.persistence.market_repository import (
    MarketSnapshotConflict,
    MarketSnapshotRepository,
)
from fkqt_jevinvestor.persistence.models import MarketFeatureRecord, MarketSnapshotRecord
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory

SessionFactory = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[SessionFactory]:
    database = tmp_path / "market-repository.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    await asyncio.to_thread(command.upgrade, config, "head")
    engine = create_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    yield create_session_factory(engine)
    await engine.dispose()


def _inputs(content_hash: str = "b" * 64) -> tuple[MarketSnapshot, dict[str, MarketFeatureSnapshot]]:
    snapshot = MarketSnapshot(
        snapshot_id="snapshot-1",
        decision_date=date(2026, 9, 18),
        decision_cutoff=datetime(2026, 9, 18, 15, tzinfo=UTC),
        next_trade_date=date(2026, 9, 21),
        calendar_complete_through=date(2026, 9, 21),
        universe_snapshot_id="universe-v1",
        universe_snapshot_hash="a" * 64,
        daily_bars={},
        security_states={},
        source_manifest_ids=("manifest-1",),
        source_audits=(
            MarketSourceAudit(
                upstream_type="FIXTURE",
                upstream_version="v1",
                request_scope={"symbols": []},
                data_cutoff=datetime(2026, 9, 18, 15, tzinfo=UTC),
                schema_version="schema-v1",
                fetched_at=datetime(2026, 9, 18, 15, tzinfo=UTC),
                record_count=0,
                raw_snapshot_ref="fixture",
                content_hash="e" * 64,
            ),
        ),
        content_hash=content_hash,
    )
    feature = FeatureValue(
        feature_code="return_20d",
        feature_version="market-features-v1",
        as_of=snapshot.decision_cutoff,
        lookback_window=20,
        value=Decimal("0.12345678"),
        missing_reason=None,
        source_snapshot_hash=content_hash,
    )
    feature_snapshot = MarketFeatureSnapshot(
        symbol="600000.SH",
        decision_date=snapshot.decision_date,
        values={feature.feature_code: feature},
        content_hash="c" * 64,
    )
    return snapshot, {feature_snapshot.symbol: feature_snapshot}


@pytest.mark.asyncio
async def test_save_is_idempotent_and_loads_versioned_features(
    session_factory: SessionFactory,
) -> None:
    repository = MarketSnapshotRepository(session_factory)
    snapshot, features = _inputs()

    first = await repository.save_run_inputs(snapshot, features, "data/snapshots/snapshot.json")
    second = await repository.save_run_inputs(snapshot, features, "data/snapshots/snapshot.json")
    loaded = await repository.load_run_inputs(snapshot.decision_date, snapshot.content_hash)

    assert first == second
    assert loaded.reference.snapshot_id == snapshot.snapshot_id
    assert loaded.features["600000.SH"].values["return_20d"].value == Decimal("0.12345678")
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MarketSnapshotRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(MarketFeatureRecord)) == 1


@pytest.mark.asyncio
async def test_same_snapshot_id_with_different_content_is_rejected_atomically(
    session_factory: SessionFactory,
) -> None:
    repository = MarketSnapshotRepository(session_factory)
    snapshot, features = _inputs()
    await repository.save_run_inputs(snapshot, features, "data/snapshots/snapshot.json")
    changed, changed_features = _inputs("d" * 64)

    with pytest.raises(MarketSnapshotConflict, match="MARKET_SNAPSHOT_CONFLICT"):
        await repository.save_run_inputs(changed, changed_features, "data/snapshots/changed.json")

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MarketSnapshotRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(MarketFeatureRecord)) == 1


@pytest.mark.asyncio
async def test_shanghai_times_and_feature_hash_round_trip_exactly(
    session_factory: SessionFactory,
) -> None:
    snapshot, features = _inputs()
    shanghai = timezone(timedelta(hours=8))
    cutoff = datetime(2026, 9, 18, 15, tzinfo=shanghai)
    snapshot = snapshot.model_copy(update={"decision_cutoff": cutoff})
    source_feature = features["600000.SH"].values["return_20d"].model_copy(
        update={"as_of": cutoff.astimezone(UTC)}
    )
    feature_snapshot = features["600000.SH"].model_copy(
        update={"values": {"return_20d": source_feature}}
    )
    repository = MarketSnapshotRepository(session_factory)

    first = await repository.save_run_inputs(
        snapshot,
        {"600000.SH": feature_snapshot},
        "data/snapshots/snapshot.json",
    )
    second = await repository.save_run_inputs(
        snapshot,
        {"600000.SH": feature_snapshot},
        "data/snapshots/snapshot.json",
    )
    loaded = await repository.load_run_inputs(snapshot.decision_date, snapshot.content_hash)

    assert first == second
    assert loaded.reference.decision_cutoff == cutoff.astimezone(UTC)
    assert loaded.features["600000.SH"] == feature_snapshot
