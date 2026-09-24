from datetime import date, datetime

import httpx
from pydantic import ValidationError

from fkqt_jevinvestor.domain.market_features import MarketSnapshot
from fkqt_jevinvestor.domain.market_time import as_utc, validate_decision_cutoff
from fkqt_jevinvestor.ingestion.canonical import sha256_json


class FkqtRestError(RuntimeError):
    pass


class FkqtRestProvider:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def freeze_snapshot(
        self,
        *,
        symbols: tuple[str, ...],
        decision_date: date,
        decision_cutoff: datetime,
        lookback_trading_days: int,
    ) -> MarketSnapshot:
        normalized_symbols = tuple(sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()}))
        if not normalized_symbols:
            raise FkqtRestError("SYMBOLS_REQUIRED")
        if lookback_trading_days <= 0:
            raise FkqtRestError("INVALID_LOOKBACK_TRADING_DAYS")

        try:
            response = await self._client.get(
                f"/api/v1/market-snapshots/{decision_date.isoformat()}",
                params={
                    "symbols": ",".join(normalized_symbols),
                    "cutoff": decision_cutoff.isoformat(),
                    "lookback_trading_days": lookback_trading_days,
                },
                headers={"Accept": "application/vnd.fkqt.market-snapshot.v1+json"},
            )
        except httpx.HTTPError as exc:
            raise FkqtRestError("UPSTREAM_UNAVAILABLE") from exc

        self._check_status(response.status_code)
        try:
            payload: object = response.json()
            snapshot = MarketSnapshot.model_validate(payload)
        except (ValueError, ValidationError) as exc:
            raise FkqtRestError("UPSTREAM_SCHEMA_INVALID") from exc

        hash_payload = snapshot.model_dump(mode="json")
        hash_payload["content_hash"] = ""
        if sha256_json(hash_payload) != snapshot.content_hash:
            raise FkqtRestError("UPSTREAM_HASH_MISMATCH")

        self._validate_snapshot(
            snapshot,
            expected_symbols=normalized_symbols,
            decision_date=decision_date,
            decision_cutoff=decision_cutoff,
        )
        return snapshot

    @staticmethod
    def _check_status(status_code: int) -> None:
        if status_code == 200:
            return
        if status_code in {401, 403}:
            raise FkqtRestError("UPSTREAM_AUTH_FAILED")
        if status_code == 404:
            raise FkqtRestError("SNAPSHOT_NOT_FOUND")
        if status_code == 429:
            raise FkqtRestError("UPSTREAM_RATE_LIMITED")
        if status_code >= 500:
            raise FkqtRestError("UPSTREAM_UNAVAILABLE")
        raise FkqtRestError("UPSTREAM_REJECTED")

    @staticmethod
    def _validate_snapshot(
        snapshot: MarketSnapshot,
        *,
        expected_symbols: tuple[str, ...],
        decision_date: date,
        decision_cutoff: datetime,
    ) -> None:
        try:
            validate_decision_cutoff(decision_date, decision_cutoff)
        except ValueError as exc:
            raise FkqtRestError("POINT_IN_TIME_VIOLATION") from exc
        if (
            snapshot.decision_date != decision_date
            or snapshot.decision_cutoff != decision_cutoff
        ):
            raise FkqtRestError("POINT_IN_TIME_VIOLATION")
        if not snapshot.source_audits or any(
            as_utc(audit.data_cutoff) > as_utc(decision_cutoff)
            for audit in snapshot.source_audits
        ):
            raise FkqtRestError("POINT_IN_TIME_VIOLATION")
        expected_scope = list(expected_symbols)
        if not any(
            audit.request_scope.get("symbols") == expected_scope
            for audit in snapshot.source_audits
        ):
            raise FkqtRestError("UPSTREAM_UNIVERSE_AUDIT_MISSING")
        if snapshot.next_trade_date <= decision_date:
            raise FkqtRestError("NEXT_TRADE_DATE_UNAVAILABLE")
        if snapshot.calendar_complete_through < snapshot.next_trade_date:
            raise FkqtRestError("UPSTREAM_CALENDAR_INCOMPLETE")

        expected = set(expected_symbols)
        if set(snapshot.daily_bars) != expected or set(snapshot.security_states) != expected:
            raise FkqtRestError("UPSTREAM_SYMBOL_COVERAGE_MISMATCH")
        for symbol, bars in snapshot.daily_bars.items():
            if any(bar.symbol != symbol or bar.trade_date > decision_date for bar in bars):
                raise FkqtRestError("POINT_IN_TIME_VIOLATION")
        for symbol, state in snapshot.security_states.items():
            if state.symbol != symbol or state.trade_date != decision_date:
                raise FkqtRestError("POINT_IN_TIME_VIOLATION")
