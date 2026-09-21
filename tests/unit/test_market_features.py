from datetime import date, timedelta
from decimal import Decimal

from fkqt_jevinvestor.domain.market_features import AdjustmentMode, DailyBar
from fkqt_jevinvestor.services.market_features import build_market_feature_snapshot


def _bars(*, mixed_adjustment: bool = False) -> tuple[DailyBar, ...]:
    start = date(2026, 1, 1)
    result: list[DailyBar] = []
    for index in range(61):
        close = Decimal(100 + index)
        previous_close = Decimal(99 + index)
        result.append(
            DailyBar(
                symbol="600000.SH",
                trade_date=start + timedelta(days=index),
                open=close - Decimal("0.5"),
                high=close + Decimal(1),
                low=close - Decimal(1),
                close=close,
                previous_close=previous_close,
                volume=Decimal(1000 + index),
                amount_cny=Decimal(10000 + index * 10),
                adjustment_mode=(
                    AdjustmentMode.QFQ if mixed_adjustment and index == 30 else AdjustmentMode.NONE
                ),
            )
        )
    return tuple(result)


def test_fixed_decimal_series_produces_expected_price_features() -> None:
    snapshot = build_market_feature_snapshot(_bars(), "a" * 64)

    assert snapshot.values["return_1d"].value == Decimal("0.00628931")
    assert snapshot.values["return_5d"].value == Decimal("0.03225806")
    assert snapshot.values["return_20d"].value == Decimal("0.14285714")
    assert snapshot.values["close_vs_ma20"].value == Decimal("0.06312292")
    assert snapshot.values["overnight_gap_pct"].value == Decimal("0.00314465")


def test_mixed_adjustment_mode_marks_price_features_missing() -> None:
    snapshot = build_market_feature_snapshot(_bars(mixed_adjustment=True), "a" * 64)

    assert snapshot.values["return_1d"].value is None
    assert snapshot.values["return_1d"].missing_reason == "ADJUSTMENT_MODE_MISMATCH"
    assert snapshot.values["volume_ratio_5d_20d"].value is not None
