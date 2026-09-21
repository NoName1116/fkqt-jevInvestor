from collections.abc import Mapping
from datetime import date, datetime
from typing import Protocol

from fkqt_jevinvestor.domain.market_features import MarketFeatureSnapshot, MarketSnapshot
from fkqt_jevinvestor.ingestion.snapshot_store import MarketSnapshotStore
from fkqt_jevinvestor.persistence.market_repository import MarketSnapshotRepository
from fkqt_jevinvestor.services.market_features import (
    apply_cross_sectional_features,
    build_market_feature_snapshot,
)


class MarketDataProvider(Protocol):
    async def freeze_snapshot(
        self,
        *,
        symbols: tuple[str, ...],
        decision_date: date,
        decision_cutoff: datetime,
        lookback_trading_days: int,
    ) -> MarketSnapshot: ...


class MarketPipeline:
    def __init__(
        self,
        provider: MarketDataProvider,
        snapshot_store: MarketSnapshotStore,
        repository: MarketSnapshotRepository,
    ) -> None:
        self._provider = provider
        self._snapshot_store = snapshot_store
        self._repository = repository

    async def freeze_and_compute(
        self,
        symbols: tuple[str, ...],
        decision_date: date,
        decision_cutoff: datetime,
        lookback_trading_days: int = 61,
    ) -> tuple[MarketSnapshot, Mapping[str, MarketFeatureSnapshot]]:
        normalized_symbols = tuple(sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()}))
        if not normalized_symbols:
            raise ValueError("SYMBOLS_REQUIRED")
        snapshot = await self._provider.freeze_snapshot(
            symbols=normalized_symbols,
            decision_date=decision_date,
            decision_cutoff=decision_cutoff,
            lookback_trading_days=lookback_trading_days,
        )
        self._validate(snapshot, normalized_symbols, decision_date, decision_cutoff)
        storage_path = self._snapshot_store.save(snapshot)
        single_symbol_features = {
            symbol: build_market_feature_snapshot(bars, snapshot.content_hash)
            for symbol, bars in sorted(snapshot.daily_bars.items())
        }
        features, _coverage = apply_cross_sectional_features(single_symbol_features)
        await self._repository.save_run_inputs(snapshot, features, str(storage_path))
        return snapshot, features

    @staticmethod
    def _validate(
        snapshot: MarketSnapshot,
        symbols: tuple[str, ...],
        decision_date: date,
        decision_cutoff: datetime,
    ) -> None:
        if snapshot.decision_date != decision_date or snapshot.decision_cutoff != decision_cutoff:
            raise ValueError("POINT_IN_TIME_VIOLATION")
        if decision_cutoff.date() != decision_date:
            raise ValueError("POINT_IN_TIME_VIOLATION")
        if snapshot.next_trade_date <= decision_date:
            raise ValueError("NEXT_TRADE_DATE_UNAVAILABLE")
        if set(snapshot.daily_bars) != set(symbols) or set(snapshot.security_states) != set(symbols):
            raise ValueError("SYMBOL_COVERAGE_MISMATCH")
        if any(
            bar.trade_date > decision_date
            for bars in snapshot.daily_bars.values()
            for bar in bars
        ):
            raise ValueError("POINT_IN_TIME_VIOLATION")
        if any(state.trade_date != decision_date for state in snapshot.security_states.values()):
            raise ValueError("POINT_IN_TIME_VIOLATION")
