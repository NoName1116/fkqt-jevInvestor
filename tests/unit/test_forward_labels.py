from datetime import date, timedelta
from decimal import Decimal

import pytest

from fkqt_jevinvestor.domain.market_features import (
    AdjustmentMode,
    DailyBar,
    SecurityTradeState,
)
from fkqt_jevinvestor.services.forward_labels import (
    PNL_LABEL_ROUND_TRIP_COST_V1,
    ForwardLabelStatus,
    build_forward_pnl_labels,
)


def _bars(
    *,
    closes: tuple[str, ...] = ("10.02", "10.03", "10.04", "10.05", "10.05"),
    lows: tuple[str, ...] = ("9.90", "9.80", "9.90", "9.95", "10.00"),
    highs: tuple[str, ...] = ("10.10", "10.20", "10.25", "10.30", "10.30"),
    open_price: str = "10",
    volume: str = "1000",
    adjustment_modes: tuple[AdjustmentMode, ...] | None = None,
) -> tuple[DailyBar, ...]:
    modes = adjustment_modes or (AdjustmentMode.QFQ,) * 5
    start = date(2026, 9, 21)
    dates = (start, start + timedelta(days=1), start + timedelta(days=2), start + timedelta(days=3), start + timedelta(days=4))
    return tuple(
        DailyBar(
            symbol="600000.SH",
            trade_date=trade_date,
            open=Decimal(open_price if index == 0 else closes[index - 1]),
            high=Decimal(highs[index]),
            low=Decimal(lows[index]),
            close=Decimal(closes[index]),
            previous_close=Decimal(open_price if index == 0 else closes[index - 1]),
            volume=Decimal(volume if index == 0 else "1000"),
            amount_cny=Decimal(10000),
            adjustment_mode=modes[index],
        )
        for index, trade_date in enumerate(dates)
    )


def _labels(
    *,
    future_bars: tuple[DailyBar, ...] | None = None,
    round_trip_cost: Decimal = PNL_LABEL_ROUND_TRIP_COST_V1,
    expected_trade_dates: tuple[date, ...] | None = None,
    d1_state: SecurityTradeState | None = None,
):
    bars = _bars() if future_bars is None else future_bars
    dates = tuple(bar.trade_date for bar in _bars())
    return build_forward_pnl_labels(
        decision_date=date(2026, 9, 18),
        future_bars=bars,
        expected_trade_dates=dates if expected_trade_dates is None else expected_trade_dates,
        d1_state=d1_state or _trade_state(dates[0]),
        round_trip_cost=round_trip_cost,
        source_snapshot_hash="a" * 64,
        calendar_snapshot_hash="b" * 64,
    )


def _trade_state(
    trade_date: date,
    *,
    trading_status: str = "TRADING",
    upper_limit_price: str = "11",
    lower_limit_price: str = "9",
) -> SecurityTradeState:
    return SecurityTradeState(
        symbol="600000.SH",
        trade_date=trade_date,
        trading_day_status="OPEN",
        trading_status=trading_status,
        is_st_or_delisting_risk=False,
        upper_limit_price=Decimal(upper_limit_price),
        lower_limit_price=Decimal(lower_limit_price),
        is_initial_no_limit_period=False,
        corporate_action_status="NONE",
        market="SSE",
        board="MAIN",
        listing_date=date(2020, 1, 1),
    )


@pytest.mark.parametrize(
    ("close", "expected"),
    [("10.030", "FLAT"), ("10.03000001", "PROFIT"), ("9.990", "FLAT"), ("9.98999999", "LOSS")],
)
def test_next_session_boundaries(close: str, expected: str) -> None:
    bars = _bars(closes=(close, close, close, close, close))
    assert _labels(future_bars=bars).next_session_pnl == expected


@pytest.mark.parametrize(
    ("close", "expected"),
    [("10.060", "FLAT"), ("10.06000001", "PROFITABLE"), ("9.960", "FLAT"), ("9.95999999", "LOSS")],
)
def test_five_day_profitability_boundaries(close: str, expected: str) -> None:
    bars = _bars(closes=("10", "10", "10", "10", close))
    assert _labels(future_bars=bars).profitability_5d == expected


@pytest.mark.parametrize(
    ("low", "expected"),
    [("9.80", "LOW"), ("9.79999999", "MEDIUM"), ("9.50", "MEDIUM"), ("9.49999999", "HIGH")],
)
def test_drawdown_boundaries(low: str, expected: str) -> None:
    bars = _bars(lows=(low,) * 5)
    assert _labels(future_bars=bars).drawdown_risk_5d == expected


@pytest.mark.parametrize(
    ("low", "high", "expected"),
    [
        ("9.80", "10.30", "UPSIDE_DOMINANT"),
        ("9.70", "10.20", "DOWNSIDE_DOMINANT"),
        ("9.80", "10.20", "BALANCED"),
        ("10.00", "10.00", "BALANCED"),
    ],
)
def test_payoff_asymmetry_boundaries(low: str, high: str, expected: str) -> None:
    bars = _bars(lows=(low,) * 5, highs=(high,) * 5, closes=("10",) * 5)
    assert _labels(future_bars=bars).payoff_asymmetry_5d == expected


def test_versioned_round_trip_cost_is_subtracted_and_audited() -> None:
    labels = _labels()
    assert labels.net_return_1d == Decimal("0.00100000")
    assert labels.net_return_5d == Decimal("0.00400000")
    assert labels.round_trip_cost == Decimal("0.00100000")
    assert labels.cost_model_version == "round-trip-cost-v1"


def test_cost_change_without_criteria_version_change_is_rejected() -> None:
    with pytest.raises(ValueError, match="PNL_LABEL_COST_CONFIG_MISMATCH"):
        _labels(round_trip_cost=Decimal("0.002"))


@pytest.mark.parametrize(
    ("future_bars", "reason"),
    [
        ((), "D1_EXECUTION_BAR_UNAVAILABLE"),
        (_bars()[:4], "FIVE_SESSION_WINDOW_INCOMPLETE"),
        (_bars(volume="0"), "D1_EXECUTION_BAR_UNAVAILABLE"),
        (tuple(reversed(_bars())), "FUTURE_BAR_DATE_MAPPING_INVALID"),
        (
            _bars(
                adjustment_modes=(
                    AdjustmentMode.QFQ,
                    AdjustmentMode.QFQ,
                    AdjustmentMode.NONE,
                    AdjustmentMode.QFQ,
                    AdjustmentMode.QFQ,
                )
            ),
            "ADJUSTMENT_MODE_MISMATCH",
        ),
    ],
)
def test_invalid_future_window_returns_unavailable(
    future_bars: tuple[DailyBar, ...], reason: str
) -> None:
    labels = _labels(future_bars=future_bars)
    assert labels.status == ForwardLabelStatus.LABEL_UNAVAILABLE
    assert labels.missing_reason == reason
    assert labels.net_return_1d is None
    assert labels.net_return_5d is None


def test_bar_on_or_before_decision_date_is_invalid_mapping() -> None:
    bars = list(_bars())
    bars[0] = bars[0].model_copy(update={"trade_date": date(2026, 9, 18)})
    labels = _labels(future_bars=tuple(bars))
    assert labels.status == ForwardLabelStatus.LABEL_UNAVAILABLE
    assert labels.missing_reason == "FUTURE_BAR_DATE_INVALID"


def test_missing_middle_trade_date_is_unavailable() -> None:
    bars = list(_bars())
    bars[2] = bars[2].model_copy(update={"trade_date": date(2026, 9, 24)})
    labels = _labels(future_bars=tuple(bars))
    assert labels.status == ForwardLabelStatus.LABEL_UNAVAILABLE
    assert labels.missing_reason == "FUTURE_BAR_DATE_MAPPING_INVALID"


@pytest.mark.parametrize("reason", ["SUSPENDED", "LIMIT_LOCKED"])
def test_d1_must_be_executable(reason: str) -> None:
    dates = tuple(bar.trade_date for bar in _bars())
    if reason == "SUSPENDED":
        labels = _labels(d1_state=_trade_state(dates[0], trading_status="SUSPENDED"))
    else:
        locked = _bars(
            open_price="11",
            closes=("11",) * 5,
            lows=("11",) * 5,
            highs=("11",) * 5,
        )
        labels = _labels(future_bars=locked)
    assert labels.status == ForwardLabelStatus.LABEL_UNAVAILABLE
    assert labels.missing_reason == "D1_EXECUTION_BAR_UNAVAILABLE"


def test_available_labels_preserve_audit_fields() -> None:
    labels = _labels()
    assert labels.status == ForwardLabelStatus.AVAILABLE
    assert labels.d1_date == date(2026, 9, 21)
    assert labels.d5_date == date(2026, 9, 25)
    assert labels.criteria_version == "pnl-label-criteria-v1"
    assert labels.source_snapshot_hash == "a" * 64
    assert labels.calendar_snapshot_hash == "b" * 64
    assert labels.missing_reason is None
