import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from fkqt_jevinvestor.domain.market_features import (
    AdjustmentMode,
    DailyBar,
    MarketSnapshot,
    SecurityTradeState,
)
from fkqt_jevinvestor.ingestion.fkqt_rest import FkqtRestError, FkqtRestProvider

SHANGHAI = timezone(timedelta(hours=8))
DECISION_DATE = date(2026, 9, 18)
DECISION_CUTOFF = datetime(2026, 9, 18, 15, 30, tzinfo=SHANGHAI)


def _payload(*, bar_date: date = DECISION_DATE) -> dict[str, object]:
    snapshot = MarketSnapshot(
        snapshot_id="snapshot-rest-v1",
        decision_date=DECISION_DATE,
        decision_cutoff=DECISION_CUTOFF,
        next_trade_date=date(2026, 9, 21),
        universe_snapshot_hash="a" * 64,
        daily_bars={
            "600000.SH": (
                DailyBar(
                    symbol="600000.SH",
                    trade_date=bar_date,
                    open=Decimal("10.00"),
                    high=Decimal("10.20"),
                    low=Decimal("9.80"),
                    close=Decimal("10.10"),
                    previous_close=Decimal("9.90"),
                    volume=Decimal(1000),
                    amount_cny=Decimal(10000),
                    adjustment_mode=AdjustmentMode.NONE,
                ),
            )
        },
        security_states={
            "600000.SH": SecurityTradeState(
                symbol="600000.SH",
                trade_date=DECISION_DATE,
                trading_day_status="OPEN",
                trading_status="TRADING",
                is_st_or_delisting_risk=False,
                upper_limit_price=None,
                lower_limit_price=None,
                is_initial_no_limit_period=None,
                corporate_action_status="UNKNOWN",
                market="SSE",
                board="MAIN",
                listing_date=date(1999, 11, 10),
                missing_reasons=("PRICE_LIMIT_DATA_UNAVAILABLE",),
            )
        },
        source_manifest_ids=("b" * 64,),
        content_hash="0" * 64,
    )
    payload = snapshot.model_dump(mode="json")
    payload["content_hash"] = ""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["content_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


async def test_rest_provider_uses_versioned_request_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/market-snapshots/2026-09-18"
        assert request.url.params["symbols"] == "600000.SH"
        assert request.url.params["cutoff"] == DECISION_CUTOFF.isoformat()
        assert request.url.params["lookback_trading_days"] == "61"
        assert request.headers["Accept"] == "application/vnd.fkqt.market-snapshot.v1+json"
        return httpx.Response(200, json=_payload())

    async with httpx.AsyncClient(
        base_url="https://fkqt.invalid",
        transport=httpx.MockTransport(handler),
    ) as client:
        snapshot = await FkqtRestProvider(client).freeze_snapshot(
            symbols=("600000.SH",),
            decision_date=DECISION_DATE,
            decision_cutoff=DECISION_CUTOFF,
            lookback_trading_days=61,
        )

    assert snapshot.decision_date == DECISION_DATE


async def test_rest_provider_rejects_content_hash_mismatch() -> None:
    payload = _payload()
    payload["content_hash"] = "0" * 64

    async with httpx.AsyncClient(
        base_url="https://fkqt.invalid",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    ) as client:
        with pytest.raises(FkqtRestError, match="UPSTREAM_HASH_MISMATCH"):
            await FkqtRestProvider(client).freeze_snapshot(
                symbols=("600000.SH",),
                decision_date=DECISION_DATE,
                decision_cutoff=DECISION_CUTOFF,
                lookback_trading_days=61,
            )


async def test_rest_provider_rejects_future_daily_bar() -> None:
    payload = _payload(bar_date=date(2026, 9, 21))

    async with httpx.AsyncClient(
        base_url="https://fkqt.invalid",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    ) as client:
        with pytest.raises(FkqtRestError, match="POINT_IN_TIME_VIOLATION"):
            await FkqtRestProvider(client).freeze_snapshot(
                symbols=("600000.SH",),
                decision_date=DECISION_DATE,
                decision_cutoff=DECISION_CUTOFF,
                lookback_trading_days=61,
            )
