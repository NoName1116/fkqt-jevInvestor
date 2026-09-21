from collections.abc import Mapping
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from itertools import pairwise
from statistics import mean
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fkqt_jevinvestor.domain.market_features import (
    DailyBar,
    FeatureValue,
    MarketFeatureSnapshot,
)
from fkqt_jevinvestor.ingestion.canonical import sha256_json

FEATURE_VERSION = "market-features-v1"
_QUANTUM = Decimal("0.00000001")
_ANNUALIZATION_FACTOR = Decimal(252).sqrt()
_CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
_CROSS_SECTIONAL_FEATURES = (
    ("return_20d", "return_20d_percentile"),
    ("realized_vol_20d", "volatility_percentile"),
    ("amount_ratio_5d_20d", "liquidity_percentile"),
)


class CrossSectionEmptyError(ValueError):
    pass


class FeatureCoverage(BaseModel):
    model_config = ConfigDict(frozen=True)

    feature_code: str
    candidate_count: int = Field(ge=0)
    valid_count: int = Field(ge=0)
    coverage_ratio: Decimal = Field(ge=0, le=1)
    missing_reason: str | None = None

    @model_validator(mode="after")
    def validate_counts_and_reason(self) -> Self:
        if self.valid_count > self.candidate_count:
            raise ValueError("valid_count must not exceed candidate_count")
        if (self.valid_count == 0) != (self.missing_reason == "CROSS_SECTION_EMPTY"):
            raise ValueError("empty coverage must use CROSS_SECTION_EMPTY")
        return self


def calculate_feature_coverage(
    feature_code: str,
    values: Mapping[str, Decimal | None],
) -> FeatureCoverage:
    candidate_count = len(values)
    valid_count = sum(value is not None for value in values.values())
    coverage_ratio = (
        Decimal(valid_count) / Decimal(candidate_count) if candidate_count else Decimal(0)
    )
    return FeatureCoverage(
        feature_code=feature_code,
        candidate_count=candidate_count,
        valid_count=valid_count,
        coverage_ratio=coverage_ratio.quantize(_QUANTUM),
        missing_reason="CROSS_SECTION_EMPTY" if valid_count == 0 else None,
    )


def percentile_ranks(values: Mapping[str, Decimal]) -> dict[str, Decimal]:
    if not values:
        raise CrossSectionEmptyError("CROSS_SECTION_EMPTY")
    if len(values) == 1:
        symbol = next(iter(values))
        return {symbol: Decimal("0.50000000")}

    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    denominator = Decimal(len(ordered) - 1)
    ranks: dict[str, Decimal] = {}
    start = 0
    while start < len(ordered):
        end = start
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[start][1]:
            end += 1
        average_rank = (Decimal(start + 1) + Decimal(end + 1)) / Decimal(2)
        percentile = ((average_rank - Decimal(1)) / denominator).quantize(_QUANTUM)
        for index in range(start, end + 1):
            ranks[ordered[index][0]] = percentile
        start = end + 1
    return {symbol: ranks[symbol] for symbol in sorted(ranks)}


def _with_content_hash(snapshot: MarketFeatureSnapshot) -> MarketFeatureSnapshot:
    payload = snapshot.model_copy(update={"content_hash": ""}).model_dump(mode="json")
    return snapshot.model_copy(update={"content_hash": sha256_json(payload)})


def apply_cross_sectional_features(
    snapshots: Mapping[str, MarketFeatureSnapshot],
) -> tuple[dict[str, MarketFeatureSnapshot], dict[str, FeatureCoverage]]:
    if any(symbol != snapshot.symbol for symbol, snapshot in snapshots.items()):
        raise ValueError("snapshot mapping key must equal snapshot symbol")

    additions: dict[str, dict[str, FeatureValue]] = {symbol: {} for symbol in snapshots}
    coverage_by_feature: dict[str, FeatureCoverage] = {}
    for source_code, output_code in _CROSS_SECTIONAL_FEATURES:
        source_values: dict[str, Decimal | None] = {}
        for symbol, snapshot in snapshots.items():
            source = snapshot.values.get(source_code)
            if source is None:
                raise ValueError(f"{source_code} missing for {symbol}")
            source_values[symbol] = source.value

        coverage = calculate_feature_coverage(source_code, source_values)
        coverage_by_feature[source_code] = coverage
        valid_values = {
            symbol: value for symbol, value in source_values.items() if value is not None
        }
        ranks = percentile_ranks(valid_values) if valid_values else {}
        for symbol, snapshot in snapshots.items():
            source = snapshot.values[source_code]
            value = ranks.get(symbol)
            if value is not None:
                missing_reason = None
            elif coverage.valid_count == 0:
                missing_reason = "CROSS_SECTION_EMPTY"
            else:
                missing_reason = source.missing_reason or "CROSS_SECTION_VALUE_MISSING"
            additions[symbol][output_code] = FeatureValue(
                feature_code=output_code,
                feature_version="cross-sectional-v1",
                as_of=source.as_of,
                lookback_window=source.lookback_window,
                value=value,
                missing_reason=missing_reason,
                source_snapshot_hash=source.source_snapshot_hash,
            )

    enriched: dict[str, MarketFeatureSnapshot] = {}
    for symbol in sorted(snapshots):
        snapshot = snapshots[symbol]
        updated = snapshot.model_copy(update={"values": {**snapshot.values, **additions[symbol]}})
        enriched[symbol] = _with_content_hash(updated)
    return enriched, coverage_by_feature


def _sample_standard_deviation(values: list[Decimal]) -> Decimal:
    if len(values) < 2:
        raise ValueError("sample standard deviation requires at least two values")
    average = mean(values)
    variance = sum((value - average) ** 2 for value in values) / Decimal(len(values) - 1)
    return variance.sqrt()


def build_market_feature_snapshot(
    bars: tuple[DailyBar, ...],
    source_snapshot_hash: str,
    *,
    float_shares: Decimal | None = None,
) -> MarketFeatureSnapshot:
    if not bars:
        raise ValueError("bars must not be empty")
    if len(source_snapshot_hash) != 64:
        raise ValueError("source_snapshot_hash must contain 64 characters")
    if len({bar.symbol for bar in bars}) != 1:
        raise ValueError("all bars must belong to the same symbol")
    if any(left.trade_date >= right.trade_date for left, right in pairwise(bars)):
        raise ValueError("bars must be strictly ordered by trade_date")
    if float_shares is not None and float_shares <= 0:
        raise ValueError("float_shares must be positive")

    latest = bars[-1]
    as_of = datetime.combine(latest.trade_date, time(15), tzinfo=_CHINA_STANDARD_TIME)
    values: dict[str, FeatureValue] = {}

    def add_value(code: str, lookback: int, value: Decimal) -> None:
        values[code] = FeatureValue(
            feature_code=code,
            feature_version=FEATURE_VERSION,
            as_of=as_of,
            lookback_window=lookback,
            value=value.quantize(_QUANTUM),
            missing_reason=None,
            source_snapshot_hash=source_snapshot_hash,
        )

    def add_missing(code: str, lookback: int, reason: str) -> None:
        values[code] = FeatureValue(
            feature_code=code,
            feature_version=FEATURE_VERSION,
            as_of=as_of,
            lookback_window=lookback,
            value=None,
            missing_reason=reason,
            source_snapshot_hash=source_snapshot_hash,
        )

    def require_history(code: str, lookback: int, required: int) -> bool:
        if len(bars) >= required:
            return True
        add_missing(code, lookback, "INSUFFICIENT_HISTORY")
        return False

    price_feature_windows = {
        "return_1d": 1,
        "return_5d": 5,
        "return_20d": 20,
        "close_vs_ma20": 20,
        "ma20_slope_5d": 25,
        "realized_vol_20d": 20,
        "downside_vol_20d": 20,
        "atr_pct_14d": 14,
        "distance_from_20d_high": 20,
        "distance_from_20d_low": 20,
        "short_term_reversal_3d": 3,
        "overnight_gap_pct": 1,
        "gap_fill_pct": 1,
    }
    adjustment_modes = {bar.adjustment_mode for bar in bars}
    if len(adjustment_modes) != 1:
        for code, lookback in price_feature_windows.items():
            add_missing(code, lookback, "ADJUSTMENT_MODE_MISMATCH")
    else:
        for days in (1, 5, 20):
            code = f"return_{days}d"
            if require_history(code, days, days + 1):
                add_value(code, days, latest.close / bars[-days - 1].close - Decimal(1))

        if require_history("close_vs_ma20", 20, 20):
            ma20 = mean(bar.close for bar in bars[-20:])
            add_value("close_vs_ma20", 20, latest.close / ma20 - Decimal(1))

        if require_history("ma20_slope_5d", 25, 25):
            current_ma20 = mean(bar.close for bar in bars[-20:])
            previous_ma20 = mean(bar.close for bar in bars[-25:-5])
            add_value("ma20_slope_5d", 25, current_ma20 / previous_ma20 - Decimal(1))

        if len(bars) >= 21:
            daily_returns = [
                bars[index].close / bars[index - 1].close - Decimal(1)
                for index in range(len(bars) - 20, len(bars))
            ]
            add_value(
                "realized_vol_20d",
                20,
                _sample_standard_deviation(daily_returns) * _ANNUALIZATION_FACTOR,
            )
            downside_returns = [min(value, Decimal(0)) for value in daily_returns]
            add_value(
                "downside_vol_20d",
                20,
                _sample_standard_deviation(downside_returns) * _ANNUALIZATION_FACTOR,
            )
        else:
            add_missing("realized_vol_20d", 20, "INSUFFICIENT_HISTORY")
            add_missing("downside_vol_20d", 20, "INSUFFICIENT_HISTORY")

        if require_history("atr_pct_14d", 14, 14):
            true_ranges = [
                max(
                    bar.high - bar.low,
                    abs(bar.high - bar.previous_close),
                    abs(bar.low - bar.previous_close),
                )
                for bar in bars[-14:]
            ]
            add_value("atr_pct_14d", 14, mean(true_ranges) / latest.close)

        if require_history("distance_from_20d_high", 20, 20):
            add_value(
                "distance_from_20d_high",
                20,
                latest.close / max(bar.high for bar in bars[-20:]) - Decimal(1),
            )
        if require_history("distance_from_20d_low", 20, 20):
            add_value(
                "distance_from_20d_low",
                20,
                latest.close / min(bar.low for bar in bars[-20:]) - Decimal(1),
            )
        if require_history("short_term_reversal_3d", 3, 4):
            reversal = -(latest.close / bars[len(bars) - 4].close - Decimal(1))
            add_value("short_term_reversal_3d", 3, reversal)

        add_value(
            "overnight_gap_pct",
            1,
            latest.open / latest.previous_close - Decimal(1),
        )
        overnight_gap = latest.open - latest.previous_close
        if overnight_gap == 0:
            add_missing("gap_fill_pct", 1, "ZERO_OVERNIGHT_GAP")
        else:
            add_value("gap_fill_pct", 1, (latest.close - latest.open) / abs(overnight_gap))

    if len(bars) < 20:
        add_missing("volume_ratio_5d_20d", 20, "INSUFFICIENT_HISTORY")
        add_missing("amount_ratio_5d_20d", 20, "INSUFFICIENT_HISTORY")
    else:
        volume_baseline = mean(bar.volume for bar in bars[-20:])
        if volume_baseline == 0:
            add_missing("volume_ratio_5d_20d", 20, "ZERO_VOLUME_BASELINE")
        else:
            add_value(
                "volume_ratio_5d_20d",
                20,
                mean(bar.volume for bar in bars[-5:]) / volume_baseline,
            )
        amount_baseline = mean(bar.amount_cny for bar in bars[-20:])
        if amount_baseline == 0:
            add_missing("amount_ratio_5d_20d", 20, "ZERO_AMOUNT_BASELINE")
        else:
            add_value(
                "amount_ratio_5d_20d",
                20,
                mean(bar.amount_cny for bar in bars[-5:]) / amount_baseline,
            )

    if float_shares is None:
        add_missing("turnover_pct", 1, "FLOAT_SHARES_UNAVAILABLE")
    else:
        add_value("turnover_pct", 1, latest.volume * Decimal(100) / float_shares)

    unhashed = MarketFeatureSnapshot(
        symbol=latest.symbol,
        decision_date=latest.trade_date,
        values=values,
        content_hash="0" * 64,
    )
    return _with_content_hash(unhashed)
