from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from fkqt_jevinvestor.domain.market import (
    MarketExecutionSnapshot,
    TradingDayStatus,
    TradingStatus,
)

BPS_DENOMINATOR = Decimal(10000)
MONEY_QUANTUM = Decimal("0.01")
PRICE_QUANTUM = Decimal("0.0001")


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class VirtualOrderStatus(StrEnum):
    PENDING_NEXT_OPEN = "PENDING_NEXT_OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    slippage_bps: Decimal = Field(default=Decimal(5), ge=0)
    commission_bps: Decimal = Field(default=Decimal(3), ge=0)
    stamp_tax_bps: Decimal = Field(default=Decimal(5), ge=0)
    lot_size: int = Field(default=100, gt=0)
    max_daily_amount_participation_pct: Decimal = Field(default=Decimal("1.00"), ge=0, le=1)

    @computed_field
    @property
    def slippage_rate(self) -> Decimal:
        return self.slippage_bps / BPS_DENOMINATOR

    @computed_field
    @property
    def commission_rate(self) -> Decimal:
        return self.commission_bps / BPS_DENOMINATOR

    @computed_field
    @property
    def stamp_tax_rate(self) -> Decimal:
        return self.stamp_tax_bps / BPS_DENOMINATOR


class FeeBreakdown(BaseModel):
    model_config = ConfigDict(frozen=True)

    commission: Decimal
    stamp_tax: Decimal

    @computed_field
    @property
    def total(self) -> Decimal:
        return self.commission + self.stamp_tax


class ExecutionGateDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    status: VirtualOrderStatus
    code: str | None = None


def fill_price(open_price: Decimal, side: OrderSide, policy: ExecutionPolicy) -> Decimal:
    direction = policy.slippage_rate if side is OrderSide.BUY else -policy.slippage_rate
    return (open_price * (Decimal(1) + direction)).quantize(
        PRICE_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def fees(value: Decimal, side: OrderSide, policy: ExecutionPolicy) -> FeeBreakdown:
    commission = (value * policy.commission_rate).quantize(MONEY_QUANTUM, ROUND_HALF_UP)
    stamp_tax = Decimal("0.00")
    if side is OrderSide.SELL:
        stamp_tax = (value * policy.stamp_tax_rate).quantize(MONEY_QUANTUM, ROUND_HALF_UP)
    return FeeBreakdown(commission=commission, stamp_tax=stamp_tax)


def round_lot(quantity: int, policy: ExecutionPolicy) -> int:
    if quantity <= 0:
        return 0
    return quantity // policy.lot_size * policy.lot_size


def capacity_quantity(
    snapshot: MarketExecutionSnapshot,
    price: Decimal,
    policy: ExecutionPolicy,
) -> int:
    if snapshot.daily_amount_cny is None or price <= 0:
        return 0
    raw = (
        snapshot.daily_amount_cny * policy.max_daily_amount_participation_pct / price
    ).to_integral_value(rounding=ROUND_DOWN)
    return round_lot(int(raw), policy)


def assess_execution_gate(
    snapshot: MarketExecutionSnapshot,
    side: OrderSide,
    policy: ExecutionPolicy,
) -> ExecutionGateDecision:
    if snapshot.trading_day_status is TradingDayStatus.UNKNOWN:
        return _blocked(VirtualOrderStatus.DATA_UNAVAILABLE, "TRADING_CALENDAR_UNAVAILABLE")
    if snapshot.trading_day_status is TradingDayStatus.CLOSED:
        return _blocked(VirtualOrderStatus.DATA_UNAVAILABLE, "MARKET_CLOSED")
    if snapshot.trading_status is TradingStatus.UNKNOWN:
        return _blocked(VirtualOrderStatus.DATA_UNAVAILABLE, "TRADING_STATUS_UNKNOWN")
    if snapshot.trading_status is TradingStatus.SUSPENDED:
        return _blocked(VirtualOrderStatus.REJECTED, "SECURITY_SUSPENDED")
    if snapshot.open_price is None:
        return _blocked(VirtualOrderStatus.DATA_UNAVAILABLE, "OPEN_PRICE_UNAVAILABLE")
    if snapshot.daily_amount_cny is None:
        return _blocked(VirtualOrderStatus.DATA_UNAVAILABLE, "CAPACITY_DATA_UNAVAILABLE")

    price = fill_price(snapshot.open_price, side, policy)
    if not snapshot.is_initial_no_limit_period:
        if snapshot.upper_limit_price is None or snapshot.lower_limit_price is None:
            return _blocked(VirtualOrderStatus.DATA_UNAVAILABLE, "PRICE_LIMIT_DATA_UNAVAILABLE")
        if side is OrderSide.BUY and price >= snapshot.upper_limit_price:
            return _blocked(VirtualOrderStatus.REJECTED, "BUY_AT_UPPER_LIMIT")
        if side is OrderSide.SELL and price <= snapshot.lower_limit_price:
            return _blocked(VirtualOrderStatus.REJECTED, "SELL_AT_LOWER_LIMIT")

    if capacity_quantity(snapshot, price, policy) < policy.lot_size:
        return _blocked(VirtualOrderStatus.EXPIRED, "ZERO_EXECUTION_CAPACITY")
    return ExecutionGateDecision(allowed=True, status=VirtualOrderStatus.PENDING_NEXT_OPEN)


def _blocked(status: VirtualOrderStatus, code: str) -> ExecutionGateDecision:
    return ExecutionGateDecision(allowed=False, status=status, code=code)
