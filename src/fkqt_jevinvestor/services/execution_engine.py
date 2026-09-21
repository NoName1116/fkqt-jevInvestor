import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.execution import (
    ExecutionPolicy,
    FeeBreakdown,
    OrderSide,
    VirtualOrderStatus,
    assess_execution_gate,
    capacity_quantity,
    fees,
    fill_price,
    round_lot,
)
from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot
from fkqt_jevinvestor.domain.portfolio import PortfolioState
from fkqt_jevinvestor.domain.signals import SignalAction, ValidatedSignalBatch
from fkqt_jevinvestor.services.portfolio_service import (
    NavState,
    PortfolioLedger,
    PortfolioSnapshot,
)

MONEY_QUANTUM = Decimal("0.01")


class VirtualOrderDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str
    idempotency_key: str
    symbol: str
    side: OrderSide
    action: SignalAction
    target_position_pct: Decimal = Field(ge=0, le=1)
    planned_execution_date: date
    policy: ExecutionPolicy
    status: VirtualOrderStatus = VirtualOrderStatus.PENDING_NEXT_OPEN
    code: str | None = None
    intended_quantity: int = Field(default=0, ge=0)
    filled_quantity: int = Field(default=0, ge=0)
    remaining_quantity: int = Field(default=0, ge=0)


class VirtualFill(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str
    sequence: int = Field(ge=1)
    symbol: str
    side: OrderSide
    quantity: int = Field(gt=0)
    raw_open_price: Decimal
    fill_price: Decimal
    gross_value: Decimal
    commission: Decimal
    stamp_tax: Decimal
    total_fees: Decimal
    trade_date: date


class ExecutionBatchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    orders: tuple[VirtualOrderDraft, ...]
    fills: tuple[VirtualFill, ...]
    snapshot: PortfolioSnapshot
    nav: NavState

    def order(self, symbol: str) -> VirtualOrderDraft:
        return next(item for item in self.orders if item.symbol == symbol)


def build_order_drafts(
    validated_batch: ValidatedSignalBatch,
    portfolio: PortfolioState,
    policy: ExecutionPolicy,
    next_trade_date: date,
) -> tuple[VirtualOrderDraft, ...]:
    drafts: list[VirtualOrderDraft] = []
    for signal in validated_batch.batch.signals:
        if signal.action in {SignalAction.HOLD, SignalAction.AVOID}:
            continue

        side = (
            OrderSide.SELL
            if signal.action in {SignalAction.CLOSE, SignalAction.REDUCE}
            else OrderSide.BUY
        )
        identity = (
            f"{portfolio.portfolio_id}:{validated_batch.input_hash}:"
            f"{signal.symbol}:{signal.action.value}:{next_trade_date.isoformat()}"
        )
        idempotency_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        drafts.append(
            VirtualOrderDraft(
                order_id=f"order-{idempotency_key[:24]}",
                idempotency_key=idempotency_key,
                symbol=signal.symbol,
                side=side,
                action=signal.action,
                target_position_pct=signal.target_position_pct,
                planned_execution_date=next_trade_date,
                policy=policy,
            )
        )
    return tuple(drafts)


def execute_order_batch(
    ledger: PortfolioLedger,
    orders: Sequence[VirtualOrderDraft],
    market_snapshots: Mapping[str, MarketExecutionSnapshot],
    trade_date: date,
) -> ExecutionBatchResult:
    if any(
        symbol != snapshot.symbol or snapshot.trade_date != trade_date
        for symbol, snapshot in market_snapshots.items()
    ):
        raise ValueError("MARKET_SNAPSHOT_MISMATCH")
    pending = [item for item in orders if item.status is VirtualOrderStatus.PENDING_NEXT_OPEN]
    opening_prices = {
        symbol: snapshot.open_price
        for symbol, snapshot in market_snapshots.items()
        if snapshot.open_price is not None
    }
    start_equity = ledger.total_equity(opening_prices)
    results: dict[str, VirtualOrderDraft] = {
        item.order_id: item for item in orders if item.status is not VirtualOrderStatus.PENDING_NEXT_OPEN
    }
    fills_out: list[VirtualFill] = []

    due: list[VirtualOrderDraft] = []
    for item in pending:
        if item.planned_execution_date != trade_date:
            results[item.order_id] = _order_result(
                item,
                status=VirtualOrderStatus.EXPIRED,
                code="SIGNAL_EXPIRED",
            )
        else:
            due.append(item)

    action_priority = {
        SignalAction.CLOSE: 0,
        SignalAction.REDUCE: 1,
        SignalAction.OPEN: 2,
        SignalAction.ADD: 3,
    }
    due.sort(key=lambda item: (action_priority[item.action], item.symbol))
    sell_orders = [item for item in due if item.side is OrderSide.SELL]
    buy_orders = [item for item in due if item.side is OrderSide.BUY]

    for item in sell_orders:
        snapshot = market_snapshots.get(item.symbol)
        if snapshot is None:
            results[item.order_id] = _order_result(
                item,
                status=VirtualOrderStatus.DATA_UNAVAILABLE,
                code="MARKET_DATA_UNAVAILABLE",
            )
            continue
        gate = assess_execution_gate(snapshot, item.side, item.policy)
        if not gate.allowed:
            results[item.order_id] = _order_result(item, status=gate.status, code=gate.code)
            continue
        assert snapshot.open_price is not None
        price = fill_price(snapshot.open_price, item.side, item.policy)
        desired_quantity = _target_quantity(start_equity, item.target_position_pct, snapshot.open_price, item.policy)
        current_quantity = ledger.position(item.symbol).quantity
        intended = round_lot(max(0, current_quantity - desired_quantity), item.policy)
        sellable = ledger.sellable_quantity(item.symbol, trade_date)
        if sellable == 0 and intended > 0:
            results[item.order_id] = _order_result(
                item,
                status=VirtualOrderStatus.REJECTED,
                code="T1_QUANTITY_UNAVAILABLE",
                intended=intended,
                remaining=intended,
            )
            continue
        quantity = min(
            intended,
            sellable,
            capacity_quantity(snapshot, price, item.policy),
        )
        quantity = round_lot(quantity, item.policy)
        if quantity == 0:
            results[item.order_id] = _order_result(
                item,
                status=VirtualOrderStatus.FILLED,
                intended=intended,
            )
            continue
        breakdown = fees(price * quantity, item.side, item.policy)
        ledger.sell_fifo(item.symbol, quantity, price, breakdown.total, trade_date)
        fill = _fill(item, len(fills_out) + 1, snapshot.open_price, price, quantity, breakdown, trade_date)
        fills_out.append(fill)
        remaining = intended - quantity
        status = VirtualOrderStatus.PARTIALLY_FILLED if remaining > 0 else VirtualOrderStatus.FILLED
        results[item.order_id] = _order_result(
            item,
            status=status,
            intended=intended,
            filled=quantity,
            remaining=remaining,
        )

    buy_candidates: list[tuple[VirtualOrderDraft, MarketExecutionSnapshot, Decimal, int, int]] = []
    for item in buy_orders:
        snapshot = market_snapshots.get(item.symbol)
        if snapshot is None:
            results[item.order_id] = _order_result(
                item,
                status=VirtualOrderStatus.DATA_UNAVAILABLE,
                code="MARKET_DATA_UNAVAILABLE",
            )
            continue
        gate = assess_execution_gate(snapshot, item.side, item.policy)
        if not gate.allowed:
            results[item.order_id] = _order_result(item, status=gate.status, code=gate.code)
            continue
        assert snapshot.open_price is not None
        price = fill_price(snapshot.open_price, item.side, item.policy)
        desired_quantity = _target_quantity(start_equity, item.target_position_pct, snapshot.open_price, item.policy)
        current_quantity = ledger.position(item.symbol).quantity
        intended = round_lot(max(0, desired_quantity - current_quantity), item.policy)
        quantity = min(intended, capacity_quantity(snapshot, price, item.policy))
        quantity = round_lot(quantity, item.policy)
        buy_candidates.append((item, snapshot, price, intended, quantity))

    required_cash = Decimal(0)
    for item, _snapshot, price, _intended, quantity in buy_candidates:
        breakdown = fees(price * quantity, item.side, item.policy)
        required_cash += price * quantity + breakdown.total
    required_cash = required_cash.quantize(MONEY_QUANTUM, ROUND_HALF_UP)

    if required_cash > ledger.cash_balance:
        for item, _snapshot, _price, intended, _quantity in buy_candidates:
            results[item.order_id] = _order_result(
                item,
                status=VirtualOrderStatus.REJECTED,
                code="INSUFFICIENT_CASH",
                intended=intended,
                remaining=intended,
            )
    else:
        for item, snapshot, price, intended, quantity in buy_candidates:
            if quantity == 0:
                results[item.order_id] = _order_result(
                    item,
                    status=VirtualOrderStatus.FILLED,
                    intended=intended,
                )
                continue
            assert snapshot.open_price is not None
            breakdown = fees(price * quantity, item.side, item.policy)
            ledger.buy(item.symbol, quantity, price, breakdown.total, trade_date)
            fill = _fill(
                item,
                len(fills_out) + 1,
                snapshot.open_price,
                price,
                quantity,
                breakdown,
                trade_date,
            )
            fills_out.append(fill)
            remaining = intended - quantity
            status = (
                VirtualOrderStatus.PARTIALLY_FILLED
                if remaining > 0
                else VirtualOrderStatus.FILLED
            )
            results[item.order_id] = _order_result(
                item,
                status=status,
                intended=intended,
                filled=quantity,
                remaining=remaining,
            )

    valuation_prices: dict[str, Decimal] = {}
    for position in ledger.positions:
        if position.quantity == 0:
            continue
        snapshot = market_snapshots.get(position.symbol)
        if snapshot is None:
            valuation_prices[position.symbol] = position.last_price
        else:
            valuation_prices[position.symbol] = (
                snapshot.unadjusted_close or snapshot.open_price or position.last_price
            )
    nav = ledger.mark_to_market(valuation_prices, trade_date)
    return ExecutionBatchResult(
        orders=tuple(results[item.order_id] for item in orders),
        fills=tuple(fills_out),
        snapshot=ledger.snapshot("POST_EXECUTION", _as_of(trade_date)),
        nav=nav,
    )


def _target_quantity(
    equity: Decimal,
    target_pct: Decimal,
    open_price: Decimal,
    policy: ExecutionPolicy,
) -> int:
    raw = (equity * target_pct / open_price).to_integral_value(rounding=ROUND_DOWN)
    return round_lot(int(raw), policy)


def _order_result(
    item: VirtualOrderDraft,
    *,
    status: VirtualOrderStatus,
    code: str | None = None,
    intended: int = 0,
    filled: int = 0,
    remaining: int = 0,
) -> VirtualOrderDraft:
    return item.model_copy(
        update={
            "status": status,
            "code": code,
            "intended_quantity": intended,
            "filled_quantity": filled,
            "remaining_quantity": remaining,
        }
    )


def _fill(
    item: VirtualOrderDraft,
    sequence: int,
    raw_open_price: Decimal,
    price: Decimal,
    quantity: int,
    breakdown: FeeBreakdown,
    trade_date: date,
) -> VirtualFill:
    gross = (price * quantity).quantize(MONEY_QUANTUM, ROUND_HALF_UP)
    return VirtualFill(
        order_id=item.order_id,
        sequence=sequence,
        symbol=item.symbol,
        side=item.side,
        quantity=quantity,
        raw_open_price=raw_open_price,
        fill_price=price,
        gross_value=gross,
        commission=breakdown.commission,
        stamp_tax=breakdown.stamp_tax,
        total_fees=breakdown.total,
        trade_date=trade_date,
    )


def _as_of(trade_date: date) -> datetime:
    return datetime.combine(trade_date, time.min, tzinfo=UTC)
