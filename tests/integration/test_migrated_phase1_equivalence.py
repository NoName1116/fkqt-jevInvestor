from decimal import Decimal
from pathlib import Path

from tests.integration.test_phase1_two_day_flow import run_two_day_fixture


def test_migrated_phase1_fixture_is_equivalent(tmp_path: Path) -> None:
    result = run_two_day_fixture(tmp_path)

    assert result == {
        "cash_balance": "795834.7700",
        "total_equity": "999834.7700",
        "sell_commission": "1.2000",
        "stamp_tax": "2.0000",
        "buy_commission": "60.0300",
        "600000.SH.quantity": 400,
        "000001.SZ.quantity": 20000,
        "duplicate_fill_count": 0,
    }
    assert abs(
        Decimal(result["cash_balance"])
        + Decimal("204000.0000")
        - Decimal(result["total_equity"])
    ) <= Decimal("0.01")
