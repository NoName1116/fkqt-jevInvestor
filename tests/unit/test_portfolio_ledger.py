from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from fkqt_jevinvestor.services.portfolio_service import AssetIdentityError, PortfolioLedger


def ledger(cash: str = "100000.00") -> PortfolioLedger:
    return PortfolioLedger(
        portfolio_id="portfolio-1",
        initial_cash=Decimal(cash),
        cash_balance=Decimal(cash),
    )


def test_buy_creates_fee_inclusive_fifo_lot() -> None:
    account = ledger()

    account.buy(
        "600000.SH",
        1000,
        Decimal("10.00"),
        Decimal("3.00"),
        date(2026, 9, 21),
    )

    position = account.position("600000.SH")
    assert account.cash_balance == Decimal("89997.00")
    assert position.quantity == 1000
    assert position.total_cost == Decimal("10003.00000000")
    assert position.average_cost == Decimal("10.00300000")
    assert position.lots[0].remaining_quantity == 1000


def test_same_day_lot_is_not_sellable_but_next_day_is() -> None:
    account = ledger()
    account.buy("600000.SH", 1000, Decimal(10), Decimal(0), date(2026, 9, 21))

    assert account.sellable_quantity("600000.SH", date(2026, 9, 21)) == 0
    assert account.sellable_quantity("600000.SH", date(2026, 9, 22)) == 1000


def test_fifo_sale_uses_oldest_cost_and_records_realized_pnl() -> None:
    account = ledger()
    account.buy("600000.SH", 100, Decimal(10), Decimal(0), date(2026, 9, 19))
    account.buy("600000.SH", 100, Decimal(12), Decimal(0), date(2026, 9, 20))

    result = account.sell_fifo(
        "600000.SH",
        100,
        Decimal(15),
        Decimal(1),
        date(2026, 9, 21),
    )

    assert result.cost_basis == Decimal("1000.00000000")
    assert result.realized_pnl == Decimal("499.00")
    assert account.realized_pnl == Decimal("499.00")
    assert account.position("600000.SH").average_cost == Decimal("12.00000000")


def test_sale_cannot_consume_more_than_t1_sellable_quantity() -> None:
    account = ledger()
    account.buy("600000.SH", 100, Decimal(10), Decimal(0), date(2026, 9, 20))
    account.buy("600000.SH", 100, Decimal(11), Decimal(0), date(2026, 9, 21))

    with pytest.raises(ValueError, match="sellable"):
        account.sell_fifo(
            "600000.SH",
            200,
            Decimal(12),
            Decimal(0),
            date(2026, 9, 21),
        )


def test_closing_position_resets_quantity_and_cost() -> None:
    account = ledger()
    account.buy("600000.SH", 100, Decimal(10), Decimal(0), date(2026, 9, 20))

    account.sell_fifo(
        "600000.SH",
        100,
        Decimal(10),
        Decimal(0),
        date(2026, 9, 21),
    )

    position = account.position("600000.SH")
    assert position.quantity == 0
    assert position.total_cost == Decimal("0E-8")
    assert position.average_cost == Decimal("0E-8")


def test_mark_to_market_builds_nav_and_stable_snapshot_hash() -> None:
    account = ledger()
    account.buy("600000.SH", 100, Decimal(10), Decimal(0), date(2026, 9, 20))

    nav = account.mark_to_market(
        {"600000.SH": Decimal(12)},
        date(2026, 9, 21),
    )
    first = account.snapshot("POST_EXECUTION", datetime(2026, 9, 21, 1, tzinfo=UTC))
    second = account.snapshot("POST_EXECUTION", datetime(2026, 9, 21, 1, tzinfo=UTC))

    assert nav.market_value == Decimal("1200.00")
    assert nav.unrealized_pnl == Decimal("200.00")
    assert nav.total_equity == Decimal("100200.00")
    assert nav.unit_nav == Decimal("1.00200000")
    assert first.content_hash == second.content_hash


def test_asset_identity_rejects_difference_above_one_cent() -> None:
    account = ledger()
    account.buy("600000.SH", 100, Decimal(10), Decimal(0), date(2026, 9, 20))
    account.mark_to_market({"600000.SH": Decimal(12)}, date(2026, 9, 21))

    with pytest.raises(AssetIdentityError):
        account.assert_asset_identity(reported_total_equity=Decimal("100199.98"))
