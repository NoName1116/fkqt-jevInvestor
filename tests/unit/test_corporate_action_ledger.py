from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from fkqt_jevinvestor.domain.corporate_actions import CorporateActionEventV1
from fkqt_jevinvestor.services.corporate_action_ledger import apply_entitlement_stage, freeze_entitlement
from fkqt_jevinvestor.services.portfolio_service import PortfolioLedger

RECORD = date(2026, 9, 24)
EX = date(2026, 9, 25)
PAY = date(2026, 9, 28)
LIST = date(2026, 9, 29)


def event(*, bonus: str = "0", cash: str = "0.2") -> CorporateActionEventV1:
    return CorporateActionEventV1(
        event_id="a" * 64, symbol="600000.SH", source_version="TUSHARE_CORPORATE_ACTION_V1",
        available_at=datetime(2026, 9, 1, tzinfo=UTC), record_date=RECORD, ex_date=EX,
        pay_date=PAY if Decimal(cash) > 0 else None,
        share_list_date=LIST if Decimal(bonus) > 0 else None,
        cash_per_share_gross=Decimal(cash), bonus_ratio=Decimal(bonus),
    )


def ledger(qty: int = 1000) -> PortfolioLedger:
    result = PortfolioLedger("p", Decimal(100000), Decimal(100000))
    result.buy("600000.SH", qty, Decimal(10), Decimal(0), date(2026, 9, 23))
    return result


def test_cash_dividend_receivable_then_payment() -> None:
    account = ledger()
    right = freeze_entitlement(event(), account, RECORD)
    right = apply_entitlement_stage(account, right, event(), stage="EX", effective_date=EX)
    assert account.dividend_receivable == Decimal("200.00")
    assert account.cash_balance == Decimal("90000.00")
    nav = account.mark_to_market({"600000.SH": Decimal("9.8")}, EX)
    assert nav.total_equity == Decimal("100000.00")
    right = apply_entitlement_stage(account, right, event(), stage="PAY", effective_date=PAY)
    assert account.cash_balance == Decimal("90200.00")
    assert account.dividend_receivable == 0
    assert account.dividend_income == Decimal("200.00")
    apply_entitlement_stage(account, right, event(), stage="PAY", effective_date=PAY)
    assert account.cash_balance == Decimal("90200.00")


def test_sold_after_record_keeps_entitlement() -> None:
    account = ledger()
    right = freeze_entitlement(event(), account, RECORD)
    account.sell_fifo("600000.SH", 1000, Decimal("9.8"), Decimal(0), EX)
    right = apply_entitlement_stage(account, right, event(), stage="EX", effective_date=EX)
    apply_entitlement_stage(account, right, event(), stage="PAY", effective_date=PAY)
    assert account.cash_balance == Decimal("100000.00")


def test_bonus_maintains_cost_and_respects_listing() -> None:
    account = ledger()
    evt = event(bonus="0.1", cash="0")
    right = freeze_entitlement(evt, account, RECORD)
    right = apply_entitlement_stage(account, right, evt, stage="EX", effective_date=EX)
    assert account.position("600000.SH").quantity == 1100
    assert account.position("600000.SH").total_cost == Decimal("10000.00000000")
    assert account.sellable_quantity("600000.SH", PAY) == 1000
    assert account.sellable_quantity("600000.SH", LIST) == 1100
    apply_entitlement_stage(account, right, evt, stage="LIST", effective_date=LIST)
    assert account.position("600000.SH").quantity == 1100


def test_fractional_bonus_stops_instead_of_rounding() -> None:
    account = ledger(1001)
    with pytest.raises(ValueError, match="CORPORATE_ACTION_FRACTIONAL_UNSUPPORTED"):
        freeze_entitlement(event(bonus="0.1"), account, RECORD)


def test_same_day_lots_share_one_bonus_cost_allocation() -> None:
    account = ledger()
    account.buy("600000.SH", 1000, Decimal(10), Decimal(0), date(2026, 9, 23))
    evt = event(bonus="0.1", cash="0")
    right = freeze_entitlement(evt, account, RECORD)
    apply_entitlement_stage(account, right, evt, stage="EX", effective_date=EX)
    bonus_cost = sum((lot.total_cost for lot in account.position(evt.symbol).lots
                      if lot.available_on == LIST), Decimal(0))
    assert bonus_cost == Decimal("1818.18181818")
    assert account.position(evt.symbol).total_cost == Decimal("20000")


def test_receivable_state_preserves_old_payload_and_new_equity() -> None:
    from fkqt_jevinvestor.domain.portfolio import PortfolioState

    old = PortfolioState(portfolio_id="p", cash_balance=Decimal(90), frozen_cash=Decimal(0),
                         realized_pnl=Decimal(0), version=1)
    assert "dividend_receivable" not in old.model_dump(mode="json")
    enriched = PortfolioState.model_validate({**old.model_dump(), "dividend_receivable": "10"})
    assert enriched.total_equity == Decimal(100)
    assert enriched.model_dump(mode="json")["dividend_receivable"] == "10"


def test_negative_dividend_amounts_are_rejected() -> None:
    with pytest.raises(ValueError):
        PortfolioLedger("p", Decimal(100), Decimal(100), dividend_receivable=Decimal(-1))


def test_bought_after_record_does_not_receive_old_dividend() -> None:
    account = ledger()
    evt = event()
    right = freeze_entitlement(evt, account, RECORD)
    account.buy(evt.symbol, 1000, Decimal("9.8"), Decimal(0), EX)
    right = apply_entitlement_stage(account, right, evt, stage="EX", effective_date=EX)
    apply_entitlement_stage(account, right, evt, stage="PAY", effective_date=PAY)
    assert account.dividend_income == Decimal(200)


def test_same_date_different_unit_costs_never_produce_negative_lot_cost() -> None:
    account = PortfolioLedger("p", Decimal(100000), Decimal(100000))
    acquired = date(2026, 9, 23)
    account.buy("600000.SH", 100, Decimal(1), Decimal(0), acquired)
    account.buy("600000.SH", 100, Decimal(100), Decimal(0), acquired)
    evt = event(bonus="0.1", cash="0")
    right = freeze_entitlement(evt, account, RECORD)
    apply_entitlement_stage(account, right, evt, stage="EX", effective_date=EX)
    assert all(lot.total_cost >= 0 for lot in account.position(evt.symbol).lots)
    assert account.position(evt.symbol).total_cost == Decimal(10100)


def test_freeze_cannot_use_wrong_record_date_or_future_lot() -> None:
    account = ledger()
    with pytest.raises(ValueError, match="CORPORATE_ACTION_RECORD_DATE_MISMATCH"):
        freeze_entitlement(event(), account, EX)
    account.buy("600000.SH", 100, Decimal(10), Decimal(0), EX)
    with pytest.raises(ValueError, match="CORPORATE_ACTION_RECORD_HISTORY_MISSING"):
        freeze_entitlement(event(), account, RECORD)
