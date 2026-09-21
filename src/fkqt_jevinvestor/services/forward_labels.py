from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from itertools import pairwise
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fkqt_jevinvestor.domain.market_features import DailyBar

_QUANTUM = Decimal("0.00000001")
_DECIMAL_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)


class ForwardLabelStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    LABEL_UNAVAILABLE = "LABEL_UNAVAILABLE"


class ForwardPnlLabelsV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: ForwardLabelStatus
    decision_date: date
    d1_date: date | None
    d5_date: date | None
    next_session_pnl: str | None
    profitability_5d: str | None
    drawdown_risk_5d: str | None
    payoff_asymmetry_5d: str | None
    net_return_1d: Decimal | None
    net_return_5d: Decimal | None
    mae_5d: Decimal | None
    mfe_5d: Decimal | None
    criteria_version: Literal["pnl-label-criteria-v1"] = "pnl-label-criteria-v1"
    source_snapshot_hash: str = Field(min_length=64, max_length=64)
    missing_reason: str | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        label_fields = (
            self.next_session_pnl,
            self.profitability_5d,
            self.drawdown_risk_5d,
            self.payoff_asymmetry_5d,
            self.net_return_1d,
            self.net_return_5d,
            self.mae_5d,
            self.mfe_5d,
        )
        if self.status is ForwardLabelStatus.AVAILABLE:
            if self.d1_date is None or self.d5_date is None or any(
                value is None for value in label_fields
            ):
                raise ValueError("AVAILABLE_FORWARD_LABEL_PAYLOAD_REQUIRED")
            if self.missing_reason is not None:
                raise ValueError("AVAILABLE_FORWARD_LABEL_REASON_FORBIDDEN")
        else:
            if any(value is not None for value in label_fields):
                raise ValueError("UNAVAILABLE_FORWARD_LABEL_PAYLOAD_FORBIDDEN")
            if not self.missing_reason:
                raise ValueError("UNAVAILABLE_FORWARD_LABEL_REASON_REQUIRED")
        return self


def _quantize(value: Decimal) -> Decimal:
    with localcontext(_DECIMAL_CONTEXT):
        return value.quantize(_QUANTUM)


def _unavailable(
    *,
    decision_date: date,
    source_snapshot_hash: str,
    missing_reason: str,
    d1_date: date | None = None,
    d5_date: date | None = None,
) -> ForwardPnlLabelsV1:
    return ForwardPnlLabelsV1(
        status=ForwardLabelStatus.LABEL_UNAVAILABLE,
        decision_date=decision_date,
        d1_date=d1_date,
        d5_date=d5_date,
        next_session_pnl=None,
        profitability_5d=None,
        drawdown_risk_5d=None,
        payoff_asymmetry_5d=None,
        net_return_1d=None,
        net_return_5d=None,
        mae_5d=None,
        mfe_5d=None,
        source_snapshot_hash=source_snapshot_hash,
        missing_reason=missing_reason,
    )


def _next_session_label(net_return: Decimal) -> str:
    if net_return > Decimal("0.002"):
        return "PROFIT"
    if net_return < Decimal("-0.002"):
        return "LOSS"
    return "FLAT"


def _profitability_label(net_return: Decimal) -> str:
    if net_return > Decimal("0.005"):
        return "PROFITABLE"
    if net_return < Decimal("-0.005"):
        return "LOSS"
    return "FLAT"


def _drawdown_label(mae: Decimal) -> str:
    drawdown = abs(mae)
    if drawdown <= Decimal("0.02"):
        return "LOW"
    if drawdown <= Decimal("0.05"):
        return "MEDIUM"
    return "HIGH"


def _payoff_label(mae: Decimal, mfe: Decimal) -> str:
    downside = abs(mae)
    if downside == 0 and mfe == 0:
        return "BALANCED"
    if mfe >= Decimal("1.5") * downside:
        return "UPSIDE_DOMINANT"
    if downside >= Decimal("1.5") * mfe:
        return "DOWNSIDE_DOMINANT"
    return "BALANCED"


def build_forward_pnl_labels(
    *,
    decision_date: date,
    future_bars: tuple[DailyBar, ...],
    round_trip_cost: Decimal,
    source_snapshot_hash: str,
) -> ForwardPnlLabelsV1:
    if round_trip_cost < 0 or round_trip_cost >= 1:
        return _unavailable(
            decision_date=decision_date,
            source_snapshot_hash=source_snapshot_hash,
            missing_reason="ROUND_TRIP_COST_INVALID",
        )
    if not future_bars:
        return _unavailable(
            decision_date=decision_date,
            source_snapshot_hash=source_snapshot_hash,
            missing_reason="D1_EXECUTION_BAR_UNAVAILABLE",
        )
    first_bar = future_bars[0]
    if len(future_bars) < 5:
        return _unavailable(
            decision_date=decision_date,
            source_snapshot_hash=source_snapshot_hash,
            missing_reason="FIVE_SESSION_WINDOW_INCOMPLETE",
            d1_date=first_bar.trade_date,
        )
    if len(future_bars) > 5:
        return _unavailable(
            decision_date=decision_date,
            source_snapshot_hash=source_snapshot_hash,
            missing_reason="FIVE_SESSION_WINDOW_INVALID",
        )

    dates = tuple(bar.trade_date for bar in future_bars)
    d1_date, d5_date = dates[0], dates[-1]
    if d1_date <= decision_date:
        return _unavailable(
            decision_date=decision_date,
            source_snapshot_hash=source_snapshot_hash,
            missing_reason="FUTURE_BAR_DATE_INVALID",
            d1_date=d1_date,
            d5_date=d5_date,
        )
    if any(current >= following for current, following in pairwise(dates)):
        return _unavailable(
            decision_date=decision_date,
            source_snapshot_hash=source_snapshot_hash,
            missing_reason="FUTURE_BAR_ORDER_INVALID",
            d1_date=d1_date,
            d5_date=d5_date,
        )
    if future_bars[0].volume <= 0 or future_bars[0].amount_cny <= 0:
        return _unavailable(
            decision_date=decision_date,
            source_snapshot_hash=source_snapshot_hash,
            missing_reason="D1_EXECUTION_BAR_UNAVAILABLE",
            d1_date=d1_date,
            d5_date=d5_date,
        )
    if len({bar.symbol for bar in future_bars}) != 1:
        return _unavailable(
            decision_date=decision_date,
            source_snapshot_hash=source_snapshot_hash,
            missing_reason="FUTURE_BAR_SYMBOL_MISMATCH",
            d1_date=d1_date,
            d5_date=d5_date,
        )
    if len({bar.adjustment_mode for bar in future_bars}) != 1:
        return _unavailable(
            decision_date=decision_date,
            source_snapshot_hash=source_snapshot_hash,
            missing_reason="ADJUSTMENT_MODE_MISMATCH",
            d1_date=d1_date,
            d5_date=d5_date,
        )

    with localcontext(_DECIMAL_CONTEXT):
        entry_open = future_bars[0].open
        net_return_1d = future_bars[0].close / entry_open - 1 - round_trip_cost
        net_return_5d = future_bars[4].close / entry_open - 1 - round_trip_cost
        mae_5d = min(
            Decimal(0),
            min(bar.low / entry_open - 1 for bar in future_bars),
        )
        mfe_5d = max(
            Decimal(0),
            max(bar.high / entry_open - 1 for bar in future_bars),
        )

    return ForwardPnlLabelsV1(
        status=ForwardLabelStatus.AVAILABLE,
        decision_date=decision_date,
        d1_date=d1_date,
        d5_date=d5_date,
        next_session_pnl=_next_session_label(net_return_1d),
        profitability_5d=_profitability_label(net_return_5d),
        drawdown_risk_5d=_drawdown_label(mae_5d),
        payoff_asymmetry_5d=_payoff_label(mae_5d, mfe_5d),
        net_return_1d=_quantize(net_return_1d),
        net_return_5d=_quantize(net_return_5d),
        mae_5d=_quantize(mae_5d),
        mfe_5d=_quantize(mfe_5d),
        source_snapshot_hash=source_snapshot_hash,
        missing_reason=None,
    )
