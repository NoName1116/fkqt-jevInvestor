from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fkqt_jevinvestor.ingestion.canonical import sha256_json

_PROBABILITY_TOLERANCE = Decimal("0.000001")


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
    model_config = ConfigDict(frozen=True)

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
    model_config = ConfigDict(frozen=True)

    header: JevStateHeaderV1
    metrics: Mapping[str, Decimal]
    coverage: Mapping[str, int | Decimal | tuple[str, ...]]


class JevSymbolStateV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    header: JevStateHeaderV1
    symbol: str = Field(min_length=1)
    security: Mapping[str, str | bool | int | None]
    features: Mapping[str, Decimal]
    missing_reasons: tuple[str, ...]


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
        return self
