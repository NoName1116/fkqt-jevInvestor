from datetime import date
from decimal import Decimal

import pytest

from fkqt_jevinvestor.domain.decision import DecisionAction
from fkqt_jevinvestor.domain.execution import (
    ExecutionPolicy,
    OrderSide,
    VirtualOrderStatus,
    assess_execution_gate,
    capacity_quantity,
    fees,
    fill_price,
    round_lot,
)
from fkqt_jevinvestor.domain.market import (
    MarketExecutionSnapshot,
    TradingDayStatus,
    TradingStatus,
)
from fkqt_jevinvestor.domain.portfolio import PortfolioState
from fkqt_jevinvestor.domain.signals import (
    FixtureSignal,
    FixtureSignalBatch,
    SignalAction,
    ValidatedSignalBatch,
)
from fkqt_jevinvestor.services.execution_engine import (
    VirtualOrderDraft,
    build_order_drafts,
    execute_order_batch,
)
from fkqt_jevinvestor.services.portfolio_service import PortfolioLedger
from fkqt_jevinvestor.services.position_sizing import (
    PositionSizingRunV1,
    SizedTargetV1,
    SizingStatus,
    to_validated_signal_batch,
)


def market(**overrides: object) -> MarketExecutionSnapshot:
    values: dict[str, object] = {
        "symbol": "600000.SH",
        "trade_date": date(2026, 9, 21),
        "trading_day_status": TradingDayStatus.OPEN,
        "trading_status": TradingStatus.TRADING,
        "open_price": Decimal("10.00"),
        "unadjusted_close": Decimal("10.10"),
        "daily_amount_cny": Decimal(1000000),
        "upper_limit_price": Decimal("11.00"),
        "lower_limit_price": Decimal("9.00"),
        "is_initial_no_limit_period": False,
    }
    values.update(overrides)
    return MarketExecutionSnapshot.model_validate(values)


@pytest.mark.parametrize(
    ("side", "expected"),
    [
        (OrderSide.BUY, Decimal("10.0050")),
        (OrderSide.SELL, Decimal("9.9950")),
    ],
)
def test_fill_price_applies_side_specific_slippage(
    side: OrderSide,
    expected: Decimal,
) -> None:
    assert fill_price(Decimal("10.00"), side, ExecutionPolicy()) == expected


def test_fees_apply_commission_and_sell_stamp_tax() -> None:
    policy = ExecutionPolicy()

    buy = fees(Decimal(100000), OrderSide.BUY, policy)
    sell = fees(Decimal(100000), OrderSide.SELL, policy)

    assert buy.commission == Decimal("30.00")
    assert buy.stamp_tax == Decimal("0.00")
    assert sell.commission == Decimal("30.00")
    assert sell.stamp_tax == Decimal("50.00")


def test_round_lot_and_capacity_use_hundred_share_lots() -> None:
    policy = ExecutionPolicy(max_daily_amount_participation_pct=Decimal("0.01"))

    assert round_lot(1099, policy) == 1000
    assert capacity_quantity(market(), Decimal(10), policy) == 1000


@pytest.mark.parametrize(
    ("snapshot", "side", "status", "code"),
    [
        (
            market(trading_day_status=TradingDayStatus.CLOSED),
            OrderSide.BUY,
            VirtualOrderStatus.DATA_UNAVAILABLE,
            "MARKET_CLOSED",
        ),
        (
            market(trading_day_status=TradingDayStatus.UNKNOWN),
            OrderSide.BUY,
            VirtualOrderStatus.DATA_UNAVAILABLE,
            "TRADING_CALENDAR_UNAVAILABLE",
        ),
        (
            market(trading_status=TradingStatus.SUSPENDED),
            OrderSide.BUY,
            VirtualOrderStatus.REJECTED,
            "SECURITY_SUSPENDED",
        ),
        (
            market(open_price=None),
            OrderSide.BUY,
            VirtualOrderStatus.DATA_UNAVAILABLE,
            "OPEN_PRICE_UNAVAILABLE",
        ),
        (
            market(open_price=Decimal("11.00")),
            OrderSide.BUY,
            VirtualOrderStatus.REJECTED,
            "BUY_AT_UPPER_LIMIT",
        ),
        (
            market(open_price=Decimal("9.00")),
            OrderSide.SELL,
            VirtualOrderStatus.REJECTED,
            "SELL_AT_LOWER_LIMIT",
        ),
    ],
)
def test_market_gate_fails_closed(
    snapshot: MarketExecutionSnapshot,
    side: OrderSide,
    status: VirtualOrderStatus,
    code: str,
) -> None:
    decision = assess_execution_gate(snapshot, side, ExecutionPolicy())

    assert decision.status is status
    assert decision.code == code


def test_capacity_below_one_lot_expires() -> None:
    snapshot = market(daily_amount_cny=Decimal(999))

    decision = assess_execution_gate(snapshot, OrderSide.BUY, ExecutionPolicy())

    assert decision.status is VirtualOrderStatus.EXPIRED
    assert decision.code == "ZERO_EXECUTION_CAPACITY"


def order(
    symbol: str,
    side: OrderSide,
    action: SignalAction,
    target: str,
    *,
    planned_date: date = date(2026, 9, 22),
) -> VirtualOrderDraft:
    return VirtualOrderDraft(
        order_id=f"order-{symbol}-{action.value}",
        idempotency_key=f"fixture:{symbol}:{action.value}:2026-09-22",
        symbol=symbol,
        side=side,
        action=action,
        target_position_pct=Decimal(target),
        planned_execution_date=planned_date,
        policy=ExecutionPolicy(),
    )


def execution_market(symbol: str, **overrides: object) -> MarketExecutionSnapshot:
    return market(symbol=symbol, trade_date=date(2026, 9, 22), **overrides)


def funded_position_ledger() -> PortfolioLedger:
    account = PortfolioLedger(
        portfolio_id="portfolio-1",
        initial_cash=Decimal(10000),
        cash_balance=Decimal(10000),
    )
    account.buy("600000.SH", 1000, Decimal(10), Decimal(0), date(2026, 9, 20))
    return account


def test_build_order_drafts_maps_actions_and_skips_noop_signals() -> None:
    fixture = FixtureSignalBatch(
        portfolio_id="portfolio-1",
        decision_date=date(2026, 9, 21),
        planned_execution_date=date(2026, 9, 22),
        fixture_version="fixture-v1",
        candidate_symbols=("600000.SH", "000001.SZ"),
        signals=(
            FixtureSignal(
                symbol="600000.SH",
                action=SignalAction.CLOSE,
                target_position_pct=Decimal(0),
                confidence=Decimal(1),
                thesis="close",
                invalidation="none",
            ),
            FixtureSignal(
                symbol="000001.SZ",
                action=SignalAction.OPEN,
                target_position_pct=Decimal("0.50"),
                confidence=Decimal(1),
                thesis="open",
                invalidation="none",
            ),
            FixtureSignal(
                symbol="000002.SZ",
                action=SignalAction.AVOID,
                target_position_pct=Decimal(0),
                confidence=Decimal(1),
                thesis="avoid",
                invalidation="none",
            ),
        ),
        cash_target_pct=Decimal("0.50"),
    )
    validated = ValidatedSignalBatch(batch=fixture, input_hash="a" * 64)
    state = PortfolioState(
        portfolio_id="portfolio-1",
        cash_balance=Decimal(10000),
        frozen_cash=Decimal(0),
        realized_pnl=Decimal(0),
        version=1,
    )

    drafts = build_order_drafts(validated, state, ExecutionPolicy(), date(2026, 9, 22))

    assert [(item.symbol, item.side) for item in drafts] == [
        ("600000.SH", OrderSide.SELL),
        ("000001.SZ", OrderSide.BUY),
    ]
    assert len({item.idempotency_key for item in drafts}) == 2


def test_blocked_enter_from_position_sizer_never_creates_order() -> None:
    run = PositionSizingRunV1(
        run_id="run-1",
        portfolio_id="portfolio-1",
        portfolio_version=1,
        decision_date=date(2026, 9, 21),
        planned_execution_date=date(2026, 9, 22),
        sizing_version="position-sizing-v1",
        config_hash="a" * 64,
        input_hash="b" * 64,
        targets=(
            SizedTargetV1(
                symbol="000001.SZ",
                decision_evaluation_id="decision-1",
                requested_action=DecisionAction.ENTER,
                status=SizingStatus.BLOCKED,
                current_position_pct=Decimal(0),
                raw_target_position_pct=Decimal(0),
                target_position_pct=Decimal(0),
                signal_action=SignalAction.AVOID,
                block_code="LIQUIDITY_ENTRY_FLOOR",
                thesis="冻结证据支持该动作。",
                invalidation="冻结证据失效。",
            ),
        ),
        gross_target_pct=Decimal(0),
        cash_target_pct=Decimal(1),
        target_batch_hash="c" * 64,
        run_code=None,
    )
    validated = to_validated_signal_batch(run)
    state = PortfolioState(
        portfolio_id="portfolio-1",
        cash_balance=Decimal(10000),
        frozen_cash=Decimal(0),
        realized_pnl=Decimal(0),
        version=1,
    )

    drafts = build_order_drafts(validated, state, ExecutionPolicy(), date(2026, 9, 22))

    assert drafts == ()


def test_execute_sells_before_buys_and_uses_released_cash() -> None:
    account = funded_position_ledger()
    orders = (
        order("000001.SZ", OrderSide.BUY, SignalAction.OPEN, "0.50"),
        order("600000.SH", OrderSide.SELL, SignalAction.CLOSE, "0"),
    )

    result = execute_order_batch(
        ledger=account,
        orders=orders,
        market_snapshots={
            "600000.SH": execution_market("600000.SH"),
            "000001.SZ": execution_market("000001.SZ"),
        },
        trade_date=date(2026, 9, 22),
    )

    assert [item.side for item in result.fills] == [OrderSide.SELL, OrderSide.BUY]
    assert result.order("600000.SH").status is VirtualOrderStatus.FILLED
    assert result.order("000001.SZ").status is VirtualOrderStatus.FILLED
    assert account.position("600000.SH").quantity == 0
    assert account.position("000001.SZ").quantity > 0


def test_failed_sale_cash_is_not_available_to_buys() -> None:
    account = funded_position_ledger()
    orders = (
        order("600000.SH", OrderSide.SELL, SignalAction.CLOSE, "0"),
        order("000001.SZ", OrderSide.BUY, SignalAction.OPEN, "0.50"),
    )

    result = execute_order_batch(
        ledger=account,
        orders=orders,
        market_snapshots={
            "600000.SH": execution_market(
                "600000.SH",
                trading_status=TradingStatus.SUSPENDED,
            ),
            "000001.SZ": execution_market("000001.SZ"),
        },
        trade_date=date(2026, 9, 22),
    )

    assert result.order("600000.SH").code == "SECURITY_SUSPENDED"
    assert result.order("000001.SZ").code == "INSUFFICIENT_CASH"
    assert result.fills == ()


def test_buy_cash_preflight_rejects_all_buys_without_resizing() -> None:
    account = PortfolioLedger(
        portfolio_id="portfolio-1",
        initial_cash=Decimal(10000),
        cash_balance=Decimal(10000),
    )
    orders = (
        order("000001.SZ", OrderSide.BUY, SignalAction.OPEN, "0.60"),
        order("000002.SZ", OrderSide.BUY, SignalAction.OPEN, "0.60"),
    )

    result = execute_order_batch(
        ledger=account,
        orders=orders,
        market_snapshots={
            "000001.SZ": execution_market("000001.SZ"),
            "000002.SZ": execution_market("000002.SZ"),
        },
        trade_date=date(2026, 9, 22),
    )

    assert {item.code for item in result.orders} == {"INSUFFICIENT_CASH"}
    assert result.fills == ()
    assert account.cash_balance == Decimal("10000.00")


def test_partial_fill_is_terminal_and_retry_is_idempotent() -> None:
    account = PortfolioLedger(
        portfolio_id="portfolio-1",
        initial_cash=Decimal(100000),
        cash_balance=Decimal(100000),
    )
    limited = execution_market("000001.SZ", daily_amount_cny=Decimal(5000))

    first = execute_order_batch(
        ledger=account,
        orders=(order("000001.SZ", OrderSide.BUY, SignalAction.OPEN, "1.00"),),
        market_snapshots={"000001.SZ": limited},
        trade_date=date(2026, 9, 22),
    )
    second = execute_order_batch(
        ledger=account,
        orders=first.orders,
        market_snapshots={"000001.SZ": limited},
        trade_date=date(2026, 9, 22),
    )

    assert first.orders[0].status is VirtualOrderStatus.PARTIALLY_FILLED
    assert second.fills == ()
    assert account.position("000001.SZ").quantity == first.orders[0].filled_quantity


def test_order_expires_outside_planned_trade_date() -> None:
    account = PortfolioLedger(
        portfolio_id="portfolio-1",
        initial_cash=Decimal(10000),
        cash_balance=Decimal(10000),
    )

    result = execute_order_batch(
        ledger=account,
        orders=(order("000001.SZ", OrderSide.BUY, SignalAction.OPEN, "1.00"),),
        market_snapshots={
            "000001.SZ": execution_market("000001.SZ").model_copy(
                update={"trade_date": date(2026, 9, 23)}
            )
        },
        trade_date=date(2026, 9, 23),
    )

    assert result.orders[0].status is VirtualOrderStatus.EXPIRED
    assert result.orders[0].code == "SIGNAL_EXPIRED"
    assert result.fills == ()


def test_t1_sellable_quantity_creates_terminal_partial_fill() -> None:
    account = PortfolioLedger(
        portfolio_id="portfolio-1",
        initial_cash=Decimal(10000),
        cash_balance=Decimal(10000),
    )
    account.buy("600000.SH", 800, Decimal(10), Decimal(0), date(2026, 9, 20))
    account.buy("600000.SH", 200, Decimal(10), Decimal(0), date(2026, 9, 22))

    result = execute_order_batch(
        ledger=account,
        orders=(order("600000.SH", OrderSide.SELL, SignalAction.CLOSE, "0"),),
        market_snapshots={"600000.SH": execution_market("600000.SH")},
        trade_date=date(2026, 9, 22),
    )

    assert result.orders[0].status is VirtualOrderStatus.PARTIALLY_FILLED
    assert result.orders[0].filled_quantity == 800
    assert result.orders[0].remaining_quantity == 200
    assert account.position("600000.SH").quantity == 200
