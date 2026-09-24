from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from fkqt_jevinvestor.domain.market_features import FeatureValue, MarketFeatureSnapshot
from fkqt_jevinvestor.services.market_features import (
    CrossSectionEmptyError,
    apply_cross_sectional_features,
    calculate_feature_coverage,
    percentile_ranks,
)


def test_percentile_ties_use_average_rank() -> None:
    values = {"A": Decimal(1), "B": Decimal(1), "C": Decimal(3)}

    first = percentile_ranks(values)
    second = percentile_ranks(dict(reversed(tuple(values.items()))))

    assert first == second
    assert first == {
        "A": Decimal("0.25000000"),
        "B": Decimal("0.25000000"),
        "C": Decimal("1.00000000"),
    }


def test_percentile_empty_has_stable_missing_reason() -> None:
    with pytest.raises(CrossSectionEmptyError, match="CROSS_SECTION_EMPTY"):
        percentile_ranks({})

    coverage = calculate_feature_coverage(
        "return_20d",
        {"A": None, "B": None},
    )
    assert coverage.candidate_count == 2
    assert coverage.valid_count == 0
    assert coverage.coverage_ratio == Decimal("0.00000000")
    assert coverage.missing_reason == "CROSS_SECTION_EMPTY"


def test_cross_sectional_mapping_adds_three_versioned_percentiles() -> None:
    snapshots: dict[str, MarketFeatureSnapshot] = {}
    for symbol, return_value in (("A", "1"), ("B", "1"), ("C", "3")):
        values = {
            code: FeatureValue(
                feature_code=code,
                feature_version="market-features-v1",
                as_of=datetime(2026, 9, 18, 15, tzinfo=UTC),
                lookback_window=20,
                value=Decimal(return_value if code == "return_20d" else "2"),
                missing_reason=None,
                source_snapshot_hash="a" * 64,
            )
            for code in ("return_20d", "realized_vol_20d", "amount_ratio_5d_20d")
        }
        snapshots[symbol] = MarketFeatureSnapshot(
            symbol=symbol,
            decision_date=date(2026, 9, 18),
            values=values,
            content_hash="b" * 64,
        )

    enriched, coverage = apply_cross_sectional_features(snapshots)

    assert enriched["A"].values["return_20d_percentile"].value == Decimal("0.25000000")
    assert enriched["C"].values["return_20d_percentile"].value == Decimal("1.00000000")
    assert enriched["A"].values["volatility_percentile"].feature_version == (
        "cross-sectional-v1"
    )
    assert "liquidity_percentile" in enriched["A"].values
    assert coverage["return_20d"].coverage_ratio == Decimal("1.00000000")
