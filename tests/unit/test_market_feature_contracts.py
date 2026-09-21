from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from fkqt_jevinvestor.domain.market_features import AdjustmentMode, DailyBar, FeatureValue


def test_daily_bar_rejects_nonpositive_prices() -> None:
    with pytest.raises(ValidationError):
        DailyBar(
            symbol="600000.SH",
            trade_date=date(2026, 9, 18),
            open=Decimal(0),
            high=Decimal("10.20"),
            low=Decimal("9.80"),
            close=Decimal("10.00"),
            previous_close=Decimal("9.90"),
            volume=Decimal(1000),
            amount_cny=Decimal(10000),
            adjustment_mode=AdjustmentMode.NONE,
        )


@pytest.mark.parametrize(
    ("value", "missing_reason"),
    [
        (Decimal("0.01"), "INSUFFICIENT_HISTORY"),
        (None, None),
    ],
)
def test_feature_value_requires_value_xor_missing_reason(
    value: Decimal | None,
    missing_reason: str | None,
) -> None:
    with pytest.raises(ValidationError):
        FeatureValue(
            feature_code="return_1d",
            feature_version="market-features-v1",
            as_of=datetime(2026, 9, 18, 15, tzinfo=UTC),
            lookback_window=1,
            value=value,
            missing_reason=missing_reason,
            source_snapshot_hash="a" * 64,
        )
