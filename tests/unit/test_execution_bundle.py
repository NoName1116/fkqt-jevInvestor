from datetime import date
from decimal import Decimal

import pytest

from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot, TradingDayStatus, TradingStatus
from fkqt_jevinvestor.ingestion.execution_bundle import ExecutionBundleV1


def test_execution_bundle_detects_tampering_and_missing_symbols() -> None:
    snapshot = MarketExecutionSnapshot(
        symbol="600000.SH",
        trade_date=date(2026, 9, 24),
        trading_day_status=TradingDayStatus.OPEN,
        trading_status=TradingStatus.TRADING,
        open_price=Decimal(10),
        unadjusted_close=Decimal("10.5"),
        daily_amount_cny=Decimal(10000000),
    )
    bundle = ExecutionBundleV1.create(date(2026, 9, 24), {"600000.SH": snapshot})
    bundle.verify(date(2026, 9, 24), {"600000.SH"})
    with pytest.raises(ValueError, match="EXECUTION_SYMBOL_COVERAGE_INCOMPLETE"):
        bundle.verify(date(2026, 9, 24), {"600000.SH", "000001.SZ"})
    with pytest.raises(ValueError, match="EXECUTION_BUNDLE_HASH_MISMATCH"):
        bundle.model_copy(update={"content_hash": "0" * 64}).verify(
            date(2026, 9, 24), {"600000.SH"}
        )
    with pytest.raises(ValueError, match="EXECUTION_BUNDLE_DATE_MISMATCH"):
        bundle.verify(date(2026, 9, 25), {"600000.SH"})
    with pytest.raises(ValueError, match="EXECUTION_TRADING_DAY_NOT_CONFIRMED"):
        ExecutionBundleV1.create(
            date(2026, 9, 24),
            {"600000.SH": snapshot.model_copy(update={
                "trading_day_status": TradingDayStatus.CLOSED,
            })},
        )
