from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from fkqt_jevinvestor.domain.market_features import (
    AdjustmentMode,
    DailyBar,
    FeatureValue,
    MarketFeatureSnapshot,
    MarketSnapshot,
    MarketSourceAudit,
    SecurityTradeState,
)
from fkqt_jevinvestor.services.jev_state_builder import build_jev_states

REQUIRED_FEATURES = (
    "return_5d",
    "return_20d",
    "return_60d",
    "close_vs_ma5",
    "close_vs_ma20",
    "close_vs_ma60",
    "ma5_slope_5d",
    "ma20_slope_5d",
    "realized_vol_20d",
    "atr_pct_14d",
    "downside_vol_20d",
    "distance_from_20d_high",
    "distance_from_20d_low",
    "short_term_reversal_3d",
    "volume_ratio_5d_20d",
    "amount_ratio_5d_20d",
    "turnover_pct",
    "liquidity_percentile",
    "return_20d_percentile",
    "volatility_percentile",
)


def _bar(symbol: str) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        trade_date=date(2026, 9, 18),
        open=Decimal(10),
        high=Decimal("10.2"),
        low=Decimal("9.8"),
        close=Decimal("10.1"),
        previous_close=Decimal(10),
        volume=Decimal(1000),
        amount_cny=Decimal(10000),
        adjustment_mode=AdjustmentMode.QFQ,
    )


def _snapshot(symbols: tuple[str, ...]) -> MarketSnapshot:
    cutoff = datetime(2026, 9, 18, 15, tzinfo=UTC)
    return MarketSnapshot(
        snapshot_id="snapshot-1",
        decision_date=date(2026, 9, 18),
        decision_cutoff=cutoff,
        next_trade_date=date(2026, 9, 21),
        calendar_complete_through=date(2026, 9, 30),
        universe_snapshot_id="universe-1",
        universe_snapshot_hash="a" * 64,
        daily_bars={symbol: (_bar(symbol),) for symbol in symbols},
        security_states={
            symbol: SecurityTradeState(
                symbol=symbol,
                trade_date=date(2026, 9, 18),
                trading_day_status="OPEN",
                trading_status="TRADING",
                is_st_or_delisting_risk=False,
                upper_limit_price=Decimal(11),
                lower_limit_price=Decimal(9),
                is_initial_no_limit_period=False,
                corporate_action_status="NONE",
                market="SSE",
                board="MAIN",
                listing_date=date(2020, 1, 1),
            )
            for symbol in symbols
        },
        source_manifest_ids=("manifest-1",),
        source_audits=(
            MarketSourceAudit(
                upstream_type="FIXTURE",
                upstream_version="v1",
                request_scope={"symbols": list(symbols)},
                data_cutoff=cutoff,
                schema_version="schema-v1",
                fetched_at=cutoff,
                record_count=len(symbols),
                raw_snapshot_ref="fixture",
                content_hash="d" * 64,
            ),
        ),
        content_hash="b" * 64,
    )


def _feature_snapshot(
    symbol: str,
    *,
    return_5d: Decimal,
    missing: str | None = None,
    as_of: datetime | None = None,
) -> MarketFeatureSnapshot:
    values: dict[str, FeatureValue] = {}
    for index, code in enumerate(REQUIRED_FEATURES, start=1):
        is_missing = code == missing
        value = return_5d if code == "return_5d" else Decimal(index) / Decimal(100)
        values[code] = FeatureValue(
            feature_code=code,
            feature_version="market-features-v1",
            as_of=as_of or datetime(2026, 9, 18, 15, tzinfo=UTC),
            lookback_window=20,
            value=None if is_missing else value,
            missing_reason="INSUFFICIENT_HISTORY" if is_missing else None,
            source_snapshot_hash="b" * 64,
        )
    return MarketFeatureSnapshot(
        symbol=symbol,
        decision_date=date(2026, 9, 18),
        values=values,
        content_hash="c" * 64,
    )


def _features(
    symbols: tuple[str, ...],
    *,
    missing_symbol: str | None = None,
) -> dict[str, MarketFeatureSnapshot]:
    return {
        symbol: _feature_snapshot(
            symbol,
            return_5d=Decimal(index - 1) / Decimal(100),
            missing="return_60d" if symbol == missing_symbol else None,
        )
        for index, symbol in enumerate(symbols)
    }


def test_candidate_limit_is_enforced_without_padding() -> None:
    twenty_one = tuple(f"S{index:02d}" for index in range(21))
    with pytest.raises(ValueError, match="CANDIDATE_TARGET_EXCEEDED"):
        build_jev_states(
            snapshot=_snapshot(twenty_one),
            features=_features(twenty_one),
            candidate_symbols=twenty_one,
            held_only_symbols=(),
            candidate_limit=20,
        )

    twelve = tuple(f"S{index:02d}" for index in range(12))
    universe, symbols = build_jev_states(
        snapshot=_snapshot(twelve),
        features=_features(twelve),
        candidate_symbols=twelve,
        held_only_symbols=(),
        candidate_limit=20,
    )
    assert universe.header.candidate_actual_size == 12
    assert len(symbols) == 12


@pytest.mark.parametrize("candidate_limit", [0, -1])
def test_candidate_limit_must_be_positive(candidate_limit: int) -> None:
    with pytest.raises(ValueError, match="CANDIDATE_LIMIT_INVALID"):
        build_jev_states(
            snapshot=_snapshot(("A",)),
            features=_features(("A",)),
            candidate_symbols=("A",),
            held_only_symbols=(),
            candidate_limit=candidate_limit,
        )


def test_universe_metrics_are_decimal_and_deterministic() -> None:
    universe, _ = build_jev_states(
        snapshot=_snapshot(("A", "B", "C")),
        features={
            "A": _feature_snapshot("A", return_5d=Decimal("-0.03")),
            "B": _feature_snapshot("B", return_5d=Decimal("0.01")),
            "C": _feature_snapshot("C", return_5d=Decimal("0.05")),
        },
        candidate_symbols=("C", "A", "B"),
        held_only_symbols=(),
        candidate_limit=20,
    )
    assert universe.metrics["median_return_5d"] == Decimal("0.01000000")
    assert universe.metrics["advance_ratio"] == Decimal("0.66666667")
    assert universe.metrics["above_ma20_ratio"] == Decimal("1.00000000")
    assert universe.coverage["coverage_ratio"] == Decimal("1.00000000")
    assert all(isinstance(value, Decimal) for value in universe.metrics.values())


def test_missing_feature_is_stable_and_reduces_coverage() -> None:
    universe, symbols = build_jev_states(
        snapshot=_snapshot(("A", "B")),
        features=_features(("A", "B"), missing_symbol="B"),
        candidate_symbols=("A", "B"),
        held_only_symbols=(),
        candidate_limit=20,
    )
    assert symbols["B"].missing_reasons == (
        "return_60d:INSUFFICIENT_HISTORY",
    )
    assert symbols["B"].security["available_feature_count"] == 19
    assert symbols["B"].security["required_feature_count"] == 20
    assert universe.coverage["missing_symbol_count"] == 1
    assert universe.coverage["coverage_ratio"] == Decimal("0.50000000")


def test_held_only_symbol_is_evaluated_but_not_in_universe_metrics() -> None:
    symbols = ("A", "B", "HELD")
    universe, symbol_states = build_jev_states(
        snapshot=_snapshot(symbols),
        features={
            "A": _feature_snapshot("A", return_5d=Decimal("0.01")),
            "B": _feature_snapshot("B", return_5d=Decimal("0.03")),
            "HELD": _feature_snapshot("HELD", return_5d=Decimal("-0.99")),
        },
        candidate_symbols=("A", "B"),
        held_only_symbols=("HELD",),
        candidate_limit=20,
    )
    assert universe.header.candidate_actual_size == 2
    assert universe.metrics["median_return_5d"] == Decimal("0.02000000")
    assert set(symbol_states) == {"A", "B", "HELD"}
    assert "HELD_ONLY" not in symbol_states["HELD"].model_dump_json()


def test_feature_after_decision_cutoff_is_rejected() -> None:
    with pytest.raises(ValueError, match="POINT_IN_TIME_VIOLATION"):
        build_jev_states(
            snapshot=_snapshot(("A",)),
            features={
                "A": _feature_snapshot(
                    "A",
                    return_5d=Decimal("0.01"),
                    as_of=datetime(2026, 9, 18, 15, tzinfo=UTC)
                    + timedelta(seconds=1),
                )
            },
            candidate_symbols=("A",),
            held_only_symbols=(),
            candidate_limit=20,
        )


@pytest.mark.parametrize("future_kind", ["bar", "security", "audit"])
def test_snapshot_future_data_is_rejected_at_jev_boundary(future_kind: str) -> None:
    snapshot = _snapshot(("A",))
    if future_kind == "bar":
        future_bar = _bar("A").model_copy(update={"trade_date": date(2026, 9, 21)})
        snapshot = snapshot.model_copy(update={"daily_bars": {"A": (future_bar,)}})
    elif future_kind == "security":
        future_state = snapshot.security_states["A"].model_copy(
            update={"trade_date": date(2026, 9, 21)}
        )
        snapshot = snapshot.model_copy(update={"security_states": {"A": future_state}})
    else:
        late_audit = snapshot.source_audits[0].model_copy(
            update={"data_cutoff": datetime(2026, 9, 18, 15, 0, 1, tzinfo=UTC)}
        )
        snapshot = snapshot.model_copy(update={"source_audits": (late_audit,)})

    with pytest.raises(ValueError, match="POINT_IN_TIME_VIOLATION"):
        build_jev_states(
            snapshot=snapshot,
            features=_features(("A",)),
            candidate_symbols=("A",),
            held_only_symbols=(),
            candidate_limit=20,
        )


def test_state_excludes_forbidden_account_and_future_fields() -> None:
    universe, symbols = build_jev_states(
        snapshot=_snapshot(("A",)),
        features=_features(("A",)),
        candidate_symbols=("A",),
        held_only_symbols=(),
        candidate_limit=20,
    )
    payload = universe.model_dump_json() + symbols["A"].model_dump_json()
    for forbidden in (
        "news",
        "cash",
        "position",
        "cost",
        "pnl",
        "future_label",
        "HELD_ONLY",
    ):
        assert forbidden.lower() not in payload.lower()
