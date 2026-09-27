from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from fkqt_jevinvestor.domain.jev_market import (
    JevStateHeaderV1,
    JevSymbolStateV1,
    JevUniverseStateV1,
)
from fkqt_jevinvestor.domain.market_features import (
    FeatureValue,
    MarketFeatureSnapshot,
    MarketSnapshot,
)
from fkqt_jevinvestor.domain.market_time import as_utc, validate_decision_cutoff

_QUANTUM = Decimal("0.00000001")
_DECIMAL_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)

REQUIRED_SYMBOL_FEATURES = (
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


def _quantize(value: Decimal) -> Decimal:
    with localcontext(_DECIMAL_CONTEXT):
        return value.quantize(_QUANTUM)


def _median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return _quantize(ordered[midpoint])
    return _quantize((ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2))


def _ratio(matches: int, total: int) -> Decimal | None:
    if total == 0:
        return None
    return _quantize(Decimal(matches) / Decimal(total))


def _sample_standard_deviation(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    if len(values) == 1:
        return Decimal("0.00000000")
    with localcontext(_DECIMAL_CONTEXT):
        average = sum(values, Decimal(0)) / Decimal(len(values))
        variance = sum((value - average) ** 2 for value in values) / Decimal(
            len(values) - 1
        )
        return _quantize(variance.sqrt())


def _require_symbols(
    snapshot: MarketSnapshot,
    features: Mapping[str, MarketFeatureSnapshot],
    symbols: Sequence[str],
) -> None:
    for symbol in symbols:
        if (
            symbol not in snapshot.daily_bars
            or not snapshot.daily_bars[symbol]
            or symbol not in snapshot.security_states
            or symbol not in features
        ):
            raise ValueError(f"SNAPSHOT_SYMBOL_COVERAGE_MISSING:{symbol}")


def _validate_snapshot_point_in_time(snapshot: MarketSnapshot) -> None:
    validate_decision_cutoff(snapshot.decision_date, snapshot.decision_cutoff)
    if not snapshot.source_audits:
        raise ValueError("SOURCE_AUDIT_REQUIRED")
    cutoff = as_utc(snapshot.decision_cutoff)
    if any(as_utc(audit.data_cutoff) > cutoff for audit in snapshot.source_audits):
        raise ValueError("POINT_IN_TIME_VIOLATION")
    if any(
        bar.trade_date > snapshot.decision_date
        for bars in snapshot.daily_bars.values()
        for bar in bars
    ):
        raise ValueError("POINT_IN_TIME_VIOLATION")
    if any(
        state.trade_date != snapshot.decision_date
        for state in snapshot.security_states.values()
    ):
        raise ValueError("POINT_IN_TIME_VIOLATION")


def _validate_feature_snapshot(
    snapshot: MarketSnapshot,
    symbol: str,
    feature_snapshot: MarketFeatureSnapshot,
) -> None:
    if feature_snapshot.symbol != symbol:
        raise ValueError(f"FEATURE_SYMBOL_MISMATCH:{symbol}")
    if feature_snapshot.decision_date != snapshot.decision_date:
        raise ValueError(f"FEATURE_DECISION_DATE_MISMATCH:{symbol}")
    for feature in feature_snapshot.values.values():
        if as_utc(feature.as_of) > as_utc(snapshot.decision_cutoff):
            raise ValueError(f"POINT_IN_TIME_VIOLATION:{symbol}:{feature.feature_code}")
        if feature.source_snapshot_hash != snapshot.content_hash:
            raise ValueError(f"FEATURE_SOURCE_SNAPSHOT_MISMATCH:{symbol}")


def _available_value(
    snapshot: MarketFeatureSnapshot,
    feature_code: str,
) -> Decimal | None:
    feature = snapshot.values.get(feature_code)
    return None if feature is None else feature.value


def _feature_version(features: Mapping[str, MarketFeatureSnapshot]) -> str:
    versions = {
        value.feature_version
        for snapshot in features.values()
        for code, value in snapshot.values.items()
        if code in REQUIRED_SYMBOL_FEATURES
    }
    if not versions:
        return "market-features-v1"
    return "+".join(sorted(versions))


def _header(
    snapshot: MarketSnapshot,
    features: Mapping[str, MarketFeatureSnapshot],
    candidate_limit: int,
    candidate_actual_size: int,
) -> JevStateHeaderV1:
    return JevStateHeaderV1(
        decision_date=snapshot.decision_date,
        decision_cutoff=snapshot.decision_cutoff,
        candidate_universe_id=snapshot.universe_snapshot_id,
        candidate_universe_hash=snapshot.universe_snapshot_hash,
        candidate_limit=candidate_limit,
        candidate_actual_size=candidate_actual_size,
        market_snapshot_hash=snapshot.content_hash,
        feature_set_version=_feature_version(features),
    )


def _symbol_state(
    *,
    header: JevStateHeaderV1,
    snapshot: MarketSnapshot,
    feature_snapshot: MarketFeatureSnapshot,
    symbol: str,
) -> JevSymbolStateV1:
    security_state = snapshot.security_states[symbol]
    bars = snapshot.daily_bars[symbol]
    current_bar = max(bars, key=lambda item: item.trade_date)
    available: dict[str, Decimal] = {}
    missing_reasons: list[str] = []
    for feature_code in REQUIRED_SYMBOL_FEATURES:
        feature: FeatureValue | None = feature_snapshot.values.get(feature_code)
        if feature is None:
            missing_reasons.append(f"{feature_code}:FEATURE_MISSING")
        elif feature.value is None:
            missing_reasons.append(f"{feature_code}:{feature.missing_reason}")
        else:
            available[feature_code] = _quantize(feature.value)

    security: dict[str, str | bool | int | None] = {
        "market": security_state.market,
        "board": security_state.board,
        "listing_age_trading_days": len(
            tuple(bar for bar in bars if bar.trade_date <= snapshot.decision_date)
        ),
        "trading_status": security_state.trading_status,
        "is_st_or_delisting_risk": security_state.is_st_or_delisting_risk,
        "is_initial_no_limit_period": security_state.is_initial_no_limit_period,
        "corporate_action_status": security_state.corporate_action_status,
        "adjustment_mode": current_bar.adjustment_mode.value,
        "available_feature_count": len(available),
        "required_feature_count": len(REQUIRED_SYMBOL_FEATURES),
    }
    return JevSymbolStateV1(
        header=header,
        symbol=symbol,
        security=security,
        features=available,
        missing_reasons=tuple(missing_reasons),
    )


def _universe_metrics(
    candidate_symbols: Sequence[str],
    features: Mapping[str, MarketFeatureSnapshot],
) -> dict[str, Decimal | None]:
    def values(feature_code: str) -> list[Decimal]:
        return [
            value
            for symbol in candidate_symbols
            if (value := _available_value(features[symbol], feature_code)) is not None
        ]

    return_5d = values("return_5d")
    return_20d = values("return_20d")
    close_vs_ma20 = values("close_vs_ma20")
    liquidity = values("liquidity_percentile")
    return {
        "median_return_5d": _median(return_5d),
        "median_return_20d": _median(return_20d),
        "median_close_vs_ma20": _median(close_vs_ma20),
        "median_ma20_slope_5d": _median(values("ma20_slope_5d")),
        "advance_ratio": _ratio(sum(value > 0 for value in return_5d), len(return_5d)),
        "above_ma20_ratio": _ratio(
            sum(value > 0 for value in close_vs_ma20), len(close_vs_ma20)
        ),
        "positive_return_20d_ratio": _ratio(
            sum(value > 0 for value in return_20d), len(return_20d)
        ),
        "median_realized_vol_20d": _median(values("realized_vol_20d")),
        "cross_section_return_dispersion": _sample_standard_deviation(return_20d),
        "downside_breadth_ratio": _ratio(
            sum(value < 0 for value in return_5d), len(return_5d)
        ),
        "median_amount_ratio_5d_20d": _median(values("amount_ratio_5d_20d")),
        "liquid_symbol_ratio": _ratio(
            sum(value >= Decimal("0.5") for value in liquidity), len(liquidity)
        ),
    }


def build_jev_states(
    *,
    snapshot: MarketSnapshot,
    features: Mapping[str, MarketFeatureSnapshot],
    candidate_symbols: tuple[str, ...],
    held_only_symbols: tuple[str, ...],
    candidate_limit: int,
) -> tuple[JevUniverseStateV1, Mapping[str, JevSymbolStateV1]]:
    _validate_snapshot_point_in_time(snapshot)
    if candidate_limit <= 0:
        raise ValueError("CANDIDATE_LIMIT_INVALID")
    if len(set(candidate_symbols)) != len(candidate_symbols):
        raise ValueError("CANDIDATE_SYMBOL_DUPLICATE")
    if len(candidate_symbols) > candidate_limit:
        raise ValueError("CANDIDATE_TARGET_EXCEEDED")
    if len(set(held_only_symbols)) != len(held_only_symbols):
        raise ValueError("HELD_ONLY_SYMBOL_DUPLICATE")

    ordered_candidates = tuple(sorted(candidate_symbols))
    evaluated_symbols = tuple(sorted(set(candidate_symbols) | set(held_only_symbols)))
    _require_symbols(snapshot, features, evaluated_symbols)
    for symbol in evaluated_symbols:
        _validate_feature_snapshot(snapshot, symbol, features[symbol])

    header = _header(snapshot, features, candidate_limit, len(ordered_candidates))
    symbol_states = {
        symbol: _symbol_state(
            header=header,
            snapshot=snapshot,
            feature_snapshot=features[symbol],
            symbol=symbol,
        )
        for symbol in evaluated_symbols
    }
    complete_candidates = sum(
        not symbol_states[symbol].missing_reasons for symbol in ordered_candidates
    )
    missing_reasons = tuple(
        sorted(
            {
                reason
                for symbol in ordered_candidates
                for reason in symbol_states[symbol].missing_reasons
            }
        )
    )
    universe = JevUniverseStateV1(
        header=header,
        metrics=_universe_metrics(ordered_candidates, features),
        coverage={
            "eligible_symbol_count": complete_candidates,
            "missing_symbol_count": len(ordered_candidates) - complete_candidates,
            "coverage_ratio": _ratio(complete_candidates, len(ordered_candidates)),
            "missing_reasons": missing_reasons,
        },
    )
    return universe, symbol_states
