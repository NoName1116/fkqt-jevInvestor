from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fkqt_jevinvestor.ingestion.canonical import sha256_json

_PROBABILITY_TOLERANCE = Decimal("0.000001")
JEV_UNIVERSE_METRIC_FIELDS = frozenset(
    {
        "median_return_5d",
        "median_return_20d",
        "median_close_vs_ma20",
        "median_ma20_slope_5d",
        "advance_ratio",
        "above_ma20_ratio",
        "positive_return_20d_ratio",
        "median_realized_vol_20d",
        "cross_section_return_dispersion",
        "downside_breadth_ratio",
        "median_amount_ratio_5d_20d",
        "liquid_symbol_ratio",
    }
)
JEV_UNIVERSE_COVERAGE_FIELDS = frozenset(
    {"eligible_symbol_count", "missing_symbol_count", "coverage_ratio", "missing_reasons"}
)
JEV_SYMBOL_SECURITY_FIELDS = frozenset(
    {
        "market",
        "board",
        "listing_age_trading_days",
        "trading_status",
        "is_st_or_delisting_risk",
        "is_initial_no_limit_period",
        "corporate_action_status",
        "adjustment_mode",
        "available_feature_count",
        "required_feature_count",
    }
)
JEV_SYMBOL_FEATURE_FIELDS = frozenset(
    {
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
    }
)

_EVALUATION_QUESTION_CONTRACTS = {
    "UNIVERSE": (
        (
            "universe_risk_regime",
            "universe-risk-regime-v1",
            "universe-risk-criteria-v1",
            ("RISK_ON", "NEUTRAL", "RISK_OFF"),
        ),
    ),
    "SYMBOL": (
        (
            "next_session_pnl",
            "next-session-pnl-v1",
            "pnl-label-criteria-v1",
            ("PROFIT", "FLAT", "LOSS"),
        ),
        (
            "profitability_5d",
            "profitability-5d-v1",
            "pnl-label-criteria-v1",
            ("PROFITABLE", "FLAT", "LOSS"),
        ),
        (
            "drawdown_risk_5d",
            "drawdown-risk-5d-v1",
            "pnl-label-criteria-v1",
            ("LOW", "MEDIUM", "HIGH"),
        ),
        (
            "payoff_asymmetry_5d",
            "payoff-asymmetry-5d-v1",
            "pnl-label-criteria-v1",
            ("UPSIDE_DOMINANT", "BALANCED", "DOWNSIDE_DOMINANT"),
        ),
        (
            "data_sufficiency",
            "data-sufficiency-v1",
            "data-sufficiency-criteria-v1",
            ("SUFFICIENT", "LIMITED", "INSUFFICIENT"),
        ),
    ),
}


class JevScope(StrEnum):
    UNIVERSE = "UNIVERSE"
    SYMBOL = "SYMBOL"


class JevEvaluationStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    AVAILABLE = "AVAILABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    CONTRACT_INVALID = "CONTRACT_INVALID"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class JevStateHeaderV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_date: date
    decision_cutoff: datetime
    candidate_universe_id: str = Field(min_length=1)
    candidate_universe_hash: str = Field(min_length=64, max_length=64)
    candidate_limit: int = Field(gt=0)
    candidate_actual_size: int = Field(ge=0)
    market_snapshot_hash: str = Field(min_length=64, max_length=64)
    feature_set_version: str = Field(min_length=1)
    state_schema_version: Literal["jev-state-v1"] = "jev-state-v1"

    @model_validator(mode="after")
    def validate_size_and_time(self) -> Self:
        if self.candidate_actual_size > self.candidate_limit:
            raise ValueError("CANDIDATE_TARGET_EXCEEDED")
        if self.decision_cutoff.tzinfo is None:
            raise ValueError("DECISION_CUTOFF_TIMEZONE_REQUIRED")
        return self


class JevUniverseStateV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    header: JevStateHeaderV1
    metrics: Mapping[str, Decimal | None]
    coverage: Mapping[str, int | Decimal | tuple[str, ...] | None]

    @model_validator(mode="after")
    def validate_field_whitelists(self) -> Self:
        if not set(self.metrics) <= JEV_UNIVERSE_METRIC_FIELDS or not set(
            self.coverage
        ) <= JEV_UNIVERSE_COVERAGE_FIELDS:
            raise ValueError("JEV_STATE_FIELD_FORBIDDEN")
        if set(self.metrics) != set(JEV_UNIVERSE_METRIC_FIELDS):
            raise ValueError("JEV_STATE_METRIC_FIELDS_INVALID")
        if set(self.coverage) != set(JEV_UNIVERSE_COVERAGE_FIELDS):
            raise ValueError("JEV_STATE_COVERAGE_FIELDS_INVALID")
        eligible = self.coverage["eligible_symbol_count"]
        missing = self.coverage["missing_symbol_count"]
        ratio = self.coverage["coverage_ratio"]
        reasons = self.coverage["missing_reasons"]
        if (
            isinstance(eligible, bool)
            or not isinstance(eligible, int)
            or eligible < 0
            or isinstance(missing, bool)
            or not isinstance(missing, int)
            or missing < 0
            or not isinstance(reasons, tuple)
            or any(not reason for reason in reasons)
            or eligible + missing != self.header.candidate_actual_size
        ):
            raise ValueError("JEV_STATE_COVERAGE_VALUES_INVALID")
        if self.header.candidate_actual_size == 0:
            if ratio is not None or eligible != 0 or missing != 0 or reasons:
                raise ValueError("JEV_STATE_COVERAGE_VALUES_INVALID")
            return self
        if (
            not isinstance(ratio, Decimal)
            or not ratio.is_finite()
            or ratio < 0
            or ratio > 1
            or ratio
            != (Decimal(eligible) / Decimal(self.header.candidate_actual_size)).quantize(
                Decimal("0.00000001")
            )
            or (missing == 0 and bool(reasons))
            or (missing > 0 and not reasons)
        ):
            raise ValueError("JEV_STATE_COVERAGE_VALUES_INVALID")
        return self


class JevSymbolStateV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    header: JevStateHeaderV1
    symbol: str = Field(min_length=1)
    security: Mapping[str, str | bool | int | None]
    features: Mapping[str, Decimal]
    missing_reasons: tuple[str, ...]

    @model_validator(mode="after")
    def validate_field_whitelists(self) -> Self:
        if not set(self.security) <= JEV_SYMBOL_SECURITY_FIELDS or not set(
            self.features
        ) <= JEV_SYMBOL_FEATURE_FIELDS:
            raise ValueError("JEV_STATE_FIELD_FORBIDDEN")
        if set(self.security) != set(JEV_SYMBOL_SECURITY_FIELDS):
            raise ValueError("JEV_STATE_SECURITY_FIELDS_INVALID")
        if (
            self.security["available_feature_count"] != len(self.features)
            or self.security["required_feature_count"]
            != len(JEV_SYMBOL_FEATURE_FIELDS)
        ):
            raise ValueError("JEV_STATE_FEATURE_COUNT_INVALID")
        missing_features = JEV_SYMBOL_FEATURE_FIELDS - set(self.features)
        reason_features: set[str] = set()
        for reason in self.missing_reasons:
            feature, separator, detail = reason.partition(":")
            if not separator or not detail or feature in reason_features:
                raise ValueError("JEV_STATE_MISSING_REASONS_INVALID")
            reason_features.add(feature)
        if reason_features != set(missing_features):
            raise ValueError("JEV_STATE_MISSING_REASONS_INVALID")
        return self


class JevQuestionResultV1(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=True)

    question_id: str = Field(min_length=1)
    question_version: str = Field(min_length=1)
    criteria_version: str = Field(min_length=1)
    label_order: tuple[str, ...] = Field(min_length=1)
    distribution: Mapping[str, Decimal]
    selected_label: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_distribution(self) -> Self:
        if len(set(self.label_order)) != len(self.label_order):
            raise ValueError("JEV_PROBABILITY_LABELS_INVALID")
        if set(self.distribution) != set(self.label_order):
            raise ValueError("JEV_PROBABILITY_LABELS_INVALID")
        probabilities = tuple(self.distribution[label] for label in self.label_order)
        if any(
            not probability.is_finite() or probability < 0 or probability > 1
            for probability in probabilities
        ):
            raise ValueError("JEV_PROBABILITY_VALUE_INVALID")
        if abs(sum(probabilities, Decimal(0)) - Decimal(1)) > _PROBABILITY_TOLERANCE:
            raise ValueError("JEV_PROBABILITY_SUM_INVALID")
        highest = max(probabilities)
        expected_label = next(
            label
            for label in self.label_order
            if self.distribution[label] == highest
        )
        if self.selected_label != expected_label:
            raise ValueError("JEV_SELECTED_LABEL_INVALID")
        return self


class JevEvaluationCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: JevScope
    state: JevUniverseStateV1 | JevSymbolStateV1
    provider_name: str = Field(min_length=1)
    provider_version: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    question_set_version: Literal["jev-pnl-questions-v1"] = "jev-pnl-questions-v1"

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if self.scope is JevScope.UNIVERSE and not isinstance(
            self.state, JevUniverseStateV1
        ):
            raise ValueError("JEV_COMMAND_SCOPE_MISMATCH")
        if self.scope is JevScope.SYMBOL and not isinstance(
            self.state, JevSymbolStateV1
        ):
            raise ValueError("JEV_COMMAND_SCOPE_MISMATCH")
        return self

    @property
    def input_hash(self) -> str:
        return sha256_json(self.state)

    @property
    def formal_key(self) -> str:
        return sha256_json(
            {
                "scope": self.scope.value,
                "input_hash": self.input_hash,
                "provider_name": self.provider_name,
                "provider_version": self.provider_version,
                "model_id": self.model_id,
                "question_set_version": self.question_set_version,
            }
        )


class JevEvaluationV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    evaluation_id: str = Field(min_length=1)
    formal_key: str = Field(min_length=64, max_length=64)
    scope: JevScope
    symbol: str | None
    status: JevEvaluationStatus
    results: tuple[JevQuestionResultV1, ...]
    provider_name: str = Field(min_length=1)
    provider_version: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    state_schema_version: str = Field(min_length=1)
    question_set_version: str = Field(min_length=1)
    input_hash: str = Field(min_length=64, max_length=64)
    raw_response_hash: str | None = Field(default=None, min_length=64, max_length=64)
    started_at: datetime
    finished_at: datetime
    latency_ms: int = Field(ge=0)
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_scope_and_status(self) -> Self:
        if self.scope is JevScope.UNIVERSE and self.symbol is not None:
            raise ValueError("JEV_UNIVERSE_SYMBOL_FORBIDDEN")
        if self.scope is JevScope.SYMBOL and not self.symbol:
            raise ValueError("JEV_SYMBOL_REQUIRED")
        if self.finished_at < self.started_at:
            raise ValueError("JEV_FINISHED_BEFORE_STARTED")
        if self.status is JevEvaluationStatus.AVAILABLE:
            if not self.results:
                raise ValueError("JEV_AVAILABLE_RESULT_REQUIRED")
            if self.error_code is not None:
                raise ValueError("JEV_AVAILABLE_ERROR_FORBIDDEN")
        elif self.status is JevEvaluationStatus.IN_PROGRESS:
            if self.results or self.error_code is not None:
                raise ValueError("JEV_IN_PROGRESS_PAYLOAD_FORBIDDEN")
        else:
            if self.results:
                raise ValueError("JEV_FAILED_RESULT_FORBIDDEN")
            if not self.error_code:
                raise ValueError("JEV_FAILED_ERROR_REQUIRED")
        question_ids = tuple(item.question_id for item in self.results)
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("JEV_DUPLICATE_QUESTION_RESULT")
        if self.status is JevEvaluationStatus.AVAILABLE:
            expected = _EVALUATION_QUESTION_CONTRACTS[self.scope.value]
            if question_ids != tuple(item[0] for item in expected):
                raise ValueError("JEV_RESULT_QUESTION_SET_INVALID")
            for result, contract in zip(self.results, expected, strict=True):
                _, question_version, criteria_version, label_order = contract
                if (
                    result.question_version != question_version
                    or result.criteria_version != criteria_version
                    or result.label_order != label_order
                ):
                    raise ValueError("JEV_RESULT_QUESTION_CONTRACT_INVALID")
        return self
