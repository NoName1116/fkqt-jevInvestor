from datetime import date
from decimal import Decimal

from fkqt_jevinvestor.operations.evaluation_metrics import (
    nav_metrics,
    probability_metrics,
    usage_cost,
)


def test_unhalved_brier_and_fixed_bins() -> None:
    result = probability_metrics([({"UP": "0.8", "DOWN": "0.2"}, "UP"),
                                  ({"UP": "0.8", "DOWN": "0.2"}, "DOWN")])
    assert result["brier"] == "0.68000000"
    assert result["accuracy"] == "0.50000000"
    assert len(result["reliability"]) == 10
    assert result["reliability"][8]["count"] == 2
    assert probability_metrics([])["brier"] is None


def test_nav_baseline_drawdown_and_gap() -> None:
    days = [date(2026, 9, 21), date(2026, 9, 22)]
    result = nav_metrics(Decimal(100), [(days[0], Decimal(110)), (days[1], Decimal(99))], days)
    assert result["interval_return"] == "-0.01000000"
    assert result["max_drawdown"] == "0.10000000"
    assert nav_metrics(Decimal(100), [(days[0], Decimal(110))], days)["interval_return"] is None


def test_unknown_usage_is_not_free_and_pricing_identity_is_strict() -> None:
    identity = {"provider": "test", "model": "model", "version": "v1", "date": "2026-09-21"}
    price = {"provider": "test", "model": "model", "version": "v1", "effective_from": "2026-09-01",
             "effective_to": "2026-09-30", "currency": "CNY", "input_per_million": "2", "output_per_million": "4"}
    assert usage_cost(None, identity, price)["estimate"] is None
    assert usage_cost({"input_tokens": 1000000, "output_tokens": 500000}, identity, price)["estimate"] == "4.00000000"
    assert usage_cost({"input_tokens": 1, "output_tokens": 1}, {**identity, "version": "v2"}, price)["estimate"] is None
