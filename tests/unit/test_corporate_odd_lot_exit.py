from datetime import date
from decimal import Decimal

import pytest

from fkqt_jevinvestor.domain.execution import OrderSide, VirtualOrderStatus
from fkqt_jevinvestor.domain.signals import SignalAction
from fkqt_jevinvestor.services.corporate_action_ledger import (
    apply_entitlement_stage,
    freeze_entitlement,
)
from fkqt_jevinvestor.services.execution_engine import execute_order_batch
from tests.unit.test_corporate_action_ledger import EX, LIST, PAY, RECORD, event, ledger
from tests.unit.test_execution_engine import market, order


@pytest.mark.parametrize(("day", "amount", "filled", "remaining", "status"), [
    (LIST, "1000000", 110, 0, VirtualOrderStatus.FILLED),
    (PAY, "1000000", 100, 10, VirtualOrderStatus.PARTIALLY_FILLED),
    (LIST, "1049.475", 100, 10, VirtualOrderStatus.PARTIALLY_FILLED),
])
def test_bonus_exit_reports_actual_target_difference(day: date, amount: str, filled: int, remaining: int, status: VirtualOrderStatus) -> None:
    account = ledger(100)
    evt = event(bonus="0.1", cash="0")
    right = freeze_entitlement(evt, account, RECORD)
    apply_entitlement_stage(account, right, evt, stage="EX", effective_date=EX)
    draft = order(evt.symbol, OrderSide.SELL, SignalAction.CLOSE, "0", planned_date=day)
    result = execute_order_batch(account, (draft,), {evt.symbol: market(trade_date=day, daily_amount_cny=Decimal(amount))}, day)
    assert result.orders[0].filled_quantity == filled
    assert result.orders[0].remaining_quantity == remaining
    assert result.orders[0].status is status
    assert account.position(evt.symbol).quantity == remaining


@pytest.mark.parametrize(("amount", "filled", "status"), [("1000", 10, VirtualOrderStatus.FILLED), ("50", 0, VirtualOrderStatus.EXPIRED)])
def test_remaining_integer_tail_can_exit_without_exceeding_capacity(amount: str, filled: int, status: VirtualOrderStatus) -> None:
    account = ledger(100)
    evt = event(bonus="0.1", cash="0")
    right = freeze_entitlement(evt, account, RECORD)
    apply_entitlement_stage(account, right, evt, stage="EX", effective_date=EX)
    account.sell_fifo(evt.symbol, 100, Decimal(10), Decimal(0), PAY)
    draft = order(evt.symbol, OrderSide.SELL, SignalAction.CLOSE, "0", planned_date=LIST)
    result = execute_order_batch(account, (draft,), {evt.symbol: market(trade_date=LIST, daily_amount_cny=Decimal(amount))}, LIST)
    assert result.orders[0].filled_quantity == filled
    assert result.orders[0].remaining_quantity == 10 - filled
    assert result.orders[0].status is status
    assert account.position(evt.symbol).quantity == 10 - filled
