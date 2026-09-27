from datetime import date
from decimal import Decimal

from fkqt_jevinvestor.api.routes.portfolios import (
    _nav_payload,  # pyright: ignore[reportPrivateUsage]
)
from fkqt_jevinvestor.services.portfolio_service import NavState


def test_nav_api_preserves_nonzero_dividend_fields_and_zero_compatibility() -> None:
    nav = NavState(valuation_date=date(2026, 9, 24), cash_balance=Decimal(100), market_value=Decimal(0), total_equity=Decimal(110), realized_pnl=Decimal(0), unrealized_pnl=Decimal(0), unit_nav=Decimal(1), daily_return=Decimal(0), cumulative_return=Decimal(0), max_drawdown=Decimal(0), dividend_receivable=Decimal(10), dividend_income=Decimal(10))
    assert _nav_payload(nav)["dividend_receivable"] == "10.00"
    assert _nav_payload(nav)["dividend_income"] == "10.00"
    zero = nav.model_copy(update={"dividend_receivable": Decimal(0), "dividend_income": Decimal(0)})
    assert "dividend_receivable" not in zero.model_dump(mode="json")
    assert "dividend_income" not in zero.model_dump(mode="json")
