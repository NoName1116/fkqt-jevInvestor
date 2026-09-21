import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.domain.market_features import (
    AdjustmentMode,
    DailyBar,
    MarketSnapshot,
    SecurityTradeState,
)
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.ingestion.snapshot_store import MarketSnapshotStore
from fkqt_jevinvestor.persistence.market_repository import MarketSnapshotRepository
from fkqt_jevinvestor.persistence.models import MarketSnapshotRecord
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.services.market_pipeline import MarketPipeline

SessionFactory = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[SessionFactory]:
    database = tmp_path / "pipeline.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    await asyncio.to_thread(command.upgrade, config, "head")
    engine = create_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    yield create_session_factory(engine)
    await engine.dispose()


def _snapshot(*, future_bar: bool = False) -> MarketSnapshot:
    decision_date = date(2026, 9, 25)
    bars = tuple(
        DailyBar(
            symbol="600000.SH",
            trade_date=decision_date - timedelta(days=60 - index),
            open=Decimal(100 + index),
            high=Decimal(101 + index),
            low=Decimal(99 + index),
            close=Decimal(100 + index),
            previous_close=Decimal(99 + index),
            volume=Decimal(1000 + index),
            amount_cny=Decimal(10000 + index),
            adjustment_mode=AdjustmentMode.NONE,
        )
        for index in range(61)
    )
    if future_bar:
        bars = (*bars[:-1], bars[-1].model_copy(update={"trade_date": date(2026, 9, 26)}))
    state = SecurityTradeState(
        symbol="600000.SH",
        trade_date=decision_date,
        trading_day_status="OPEN",
        trading_status="TRADING",
        is_st_or_delisting_risk=False,
        upper_limit_price=Decimal(176),
        lower_limit_price=Decimal(144),
        is_initial_no_limit_period=False,
        corporate_action_status="NONE",
        market="SH",
        board="MAIN",
        listing_date=date(1999, 11, 10),
    )
    unhashed = MarketSnapshot(
        snapshot_id="snapshot-friday",
        decision_date=decision_date,
        decision_cutoff=datetime(2026, 9, 25, 15, tzinfo=UTC),
        next_trade_date=date(2026, 9, 28),
        universe_snapshot_hash="a" * 64,
        daily_bars={"600000.SH": bars},
        security_states={"600000.SH": state},
        source_manifest_ids=("manifest-1",),
        content_hash="0" * 64,
    )
    payload = unhashed.model_copy(update={"content_hash": ""}).model_dump(mode="json")
    return unhashed.model_copy(update={"content_hash": sha256_json(payload)})


class FakeProvider:
    def __init__(self, snapshot: MarketSnapshot) -> None:
        self.snapshot = snapshot

    async def freeze_snapshot(self, **_kwargs: object) -> MarketSnapshot:
        return self.snapshot


@pytest.mark.asyncio
async def test_pipeline_uses_calendar_next_day_and_persists_features(
    session_factory: SessionFactory,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    pipeline = MarketPipeline(
        FakeProvider(snapshot),
        MarketSnapshotStore(tmp_path / "snapshots"),
        MarketSnapshotRepository(session_factory),
    )

    frozen, features = await pipeline.freeze_and_compute(
        ("600000.SH",), snapshot.decision_date, snapshot.decision_cutoff
    )

    assert frozen.next_trade_date == date(2026, 9, 28)
    assert features["600000.SH"].values["return_20d_percentile"].value == Decimal(
        "0.50000000"
    )


@pytest.mark.asyncio
async def test_pipeline_rejects_future_bar_before_database_write(
    session_factory: SessionFactory,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(future_bar=True)
    pipeline = MarketPipeline(
        FakeProvider(snapshot),
        MarketSnapshotStore(tmp_path / "snapshots"),
        MarketSnapshotRepository(session_factory),
    )

    with pytest.raises(ValueError, match="POINT_IN_TIME_VIOLATION"):
        await pipeline.freeze_and_compute(
            ("600000.SH",), snapshot.decision_date, snapshot.decision_cutoff
        )

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MarketSnapshotRecord)) == 0
