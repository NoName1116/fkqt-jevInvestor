import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
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
        universe_snapshot_hash="a" * 64,
        daily_bars={},
        security_states={},
        source_manifest_ids=("manifest-1",),
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
