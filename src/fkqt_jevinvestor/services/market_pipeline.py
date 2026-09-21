from collections.abc import Mapping
from datetime import date, datetime
from typing import Protocol

from fkqt_jevinvestor.domain.market_features import MarketFeatureSnapshot, MarketSnapshot
from fkqt_jevinvestor.domain.market_time import as_utc, validate_decision_cutoff
from fkqt_jevinvestor.ingestion.snapshot_store import MarketSnapshotStore
from fkqt_jevinvestor.persistence.market_repository import MarketSnapshotRepository
from fkqt_jevinvestor.services.market_features import (
    apply_cross_sectional_features,
    build_market_feature_snapshot,
    build_missing_market_feature_snapshot,
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
        single_symbol_features: dict[str, MarketFeatureSnapshot] = {}
        for symbol, bars in sorted(snapshot.daily_bars.items()):
            if not bars:
                state_reasons = snapshot.security_states[symbol].missing_reasons
                reason = state_reasons[0] if state_reasons else "DAILY_BARS_MISSING"
                single_symbol_features[symbol] = build_missing_market_feature_snapshot(
                    symbol,
                    decision_date,
                    snapshot.content_hash,
                    reason,
                )
            elif bars[-1].trade_date != decision_date:
                single_symbol_features[symbol] = build_missing_market_feature_snapshot(
                    symbol,
                    decision_date,
                    snapshot.content_hash,
                    "DAILY_BAR_STALE",
                )
            else:
                single_symbol_features[symbol] = build_market_feature_snapshot(
                    bars,
                    snapshot.content_hash,
                )
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
        validate_decision_cutoff(decision_date, decision_cutoff)
        if not snapshot.source_audits:
            raise ValueError("SOURCE_AUDIT_REQUIRED")
        if any(
            as_utc(audit.data_cutoff) > as_utc(decision_cutoff)
            for audit in snapshot.source_audits
        ):
            raise ValueError("POINT_IN_TIME_VIOLATION")
        if not snapshot.universe_snapshot_id or not any(
            audit.request_scope.get("symbols") == list(symbols)
            for audit in snapshot.source_audits
        ):
            raise ValueError("UNIVERSE_AUDIT_MISSING")
        if snapshot.next_trade_date <= decision_date:
            raise ValueError("NEXT_TRADE_DATE_UNAVAILABLE")
        if snapshot.calendar_complete_through < snapshot.next_trade_date:
            raise ValueError("TRADING_CALENDAR_INCOMPLETE")
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
