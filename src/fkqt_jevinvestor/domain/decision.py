from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fkqt_jevinvestor.domain.jev_market import (
    JevEvaluationStatus,
    JevEvaluationV1,
    JevScope,
)
from fkqt_jevinvestor.domain.market_features import MarketFeatureSnapshot
from fkqt_jevinvestor.domain.portfolio import PortfolioState, PositionState
from fkqt_jevinvestor.ingestion.canonical import sha256_json

DECISION_INPUT_SCHEMA_VERSION = "decision-input-v1"
DECISION_PROMPT_VERSION = "decision-prompt-v1"
DECISION_OUTPUT_SCHEMA_VERSION = "decision-output-v1"


class DecisionMembership(StrEnum):
    CANDIDATE = "CANDIDATE"
    HELD_ONLY = "HELD_ONLY"


class DecisionAction(StrEnum):
    ENTER = "ENTER"
    KEEP = "KEEP"
    EXIT = "EXIT"
    AVOID = "AVOID"
    NO_SIGNAL = "NO_SIGNAL"


class DecisionEvaluationStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    AVAILABLE = "AVAILABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    CONTRACT_INVALID = "CONTRACT_INVALID"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class DecisionHistoryV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_date: date
    action: DecisionAction


class PendingOrderSummaryV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str = Field(min_length=1)
    planned_execution_date: date
    status: str = Field(min_length=1)


class DecisionInputV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_date: date
    decision_cutoff: datetime
    planned_execution_date: date
    candidate_universe_id: str = Field(min_length=1)
    candidate_universe_hash: str = Field(min_length=64, max_length=64)
    market_snapshot_hash: str = Field(min_length=64, max_length=64)
    feature_snapshot: MarketFeatureSnapshot | None
    symbol: str = Field(min_length=1)
    membership: DecisionMembership
    universe_jev: JevEvaluationV1
    symbol_jev: JevEvaluationV1
    portfolio: PortfolioState
    total_equity: Decimal = Field(gt=0)
    position: PositionState | None
    recent_actions: tuple[DecisionHistoryV1, ...]
    pending_orders: tuple[PendingOrderSummaryV1, ...]
    allowed_actions: tuple[DecisionAction, ...]
    input_schema_version: Literal["decision-input-v1"] = DECISION_INPUT_SCHEMA_VERSION

    @model_validator(mode="after")
    def validate_frozen_decision_state(self) -> Self:
        if self.decision_cutoff.tzinfo is None:
            raise ValueError("DECISION_CUTOFF_TIMEZONE_REQUIRED")
        if self.decision_cutoff.date() != self.decision_date:
            raise ValueError("DECISION_CUTOFF_DATE_MISMATCH")
        if self.planned_execution_date <= self.decision_date:
            raise ValueError("DECISION_EXECUTION_DATE_INVALID")
        if self.feature_snapshot is not None:
            if (
                self.feature_snapshot.symbol != self.symbol
                or self.feature_snapshot.decision_date != self.decision_date
            ):
                raise ValueError("DECISION_FEATURE_IDENTITY_MISMATCH")
            if any(
                feature.as_of > self.decision_cutoff
                for feature in self.feature_snapshot.values.values()
            ):
                raise ValueError("DECISION_POINT_IN_TIME_VIOLATION")
        if (
            self.universe_jev.finished_at > self.decision_cutoff
            or self.symbol_jev.finished_at > self.decision_cutoff
        ):
            raise ValueError("DECISION_POINT_IN_TIME_VIOLATION")
        if (
            self.universe_jev.scope is not JevScope.UNIVERSE
            or self.symbol_jev.scope is not JevScope.SYMBOL
            or self.symbol_jev.symbol != self.symbol
        ):
            raise ValueError("DECISION_JEV_EVIDENCE_INVALID")
        if self.position is not None and self.position.symbol != self.symbol:
            raise ValueError("DECISION_POSITION_SYMBOL_MISMATCH")
        if self.membership is DecisionMembership.HELD_ONLY and self.position is None:
            raise ValueError("DECISION_HELD_ONLY_POSITION_REQUIRED")
        expected_actions = (
            (DecisionAction.ENTER, DecisionAction.AVOID)
            if self.position is None
            else (DecisionAction.KEEP, DecisionAction.EXIT)
        )
        if self.allowed_actions != expected_actions:
            raise ValueError("DECISION_ALLOWED_ACTIONS_INVALID")
        if len(self.recent_actions) > 5:
            raise ValueError("DECISION_HISTORY_LIMIT_EXCEEDED")
        if tuple(sorted(self.recent_actions, key=lambda item: item.decision_date)) != (
            self.recent_actions
        ):
            raise ValueError("DECISION_HISTORY_ORDER_INVALID")
        return self

    @property
    def input_hash(self) -> str:
        return sha256_json(self)


class DecisionModelOutputV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["ENTER", "KEEP", "EXIT", "AVOID"]
    thesis: str = Field(min_length=1, max_length=240)
    invalidation: str = Field(min_length=1, max_length=240)


class DecisionEvaluationCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_input: DecisionInputV1
    provider_name: str = Field(min_length=1)
    provider_version: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    prompt_version: Literal["decision-prompt-v1"] = DECISION_PROMPT_VERSION
    output_schema_version: Literal["decision-output-v1"] = (
        DECISION_OUTPUT_SCHEMA_VERSION
    )

    @property
    def input_hash(self) -> str:
        return self.decision_input.input_hash

    @property
    def formal_key(self) -> str:
        return sha256_json(
            {
                "input_hash": self.input_hash,
                "provider_name": self.provider_name,
                "provider_version": self.provider_version,
                "model_id": self.model_id,
                "prompt_version": self.prompt_version,
                "output_schema_version": self.output_schema_version,
            }
        )


class DecisionProviderResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    output: DecisionModelOutputV1
    raw_response_text: str = Field(min_length=1, max_length=4096)
    raw_response_hash: str = Field(min_length=64, max_length=64)


class DecisionEvaluationV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluation_id: str = Field(min_length=1)
    formal_key: str = Field(min_length=64, max_length=64)
    input_hash: str = Field(min_length=64, max_length=64)
    symbol: str = Field(min_length=1)
    status: DecisionEvaluationStatus
    action: DecisionAction
    thesis: str | None = Field(default=None, min_length=1, max_length=240)
    invalidation: str | None = Field(default=None, min_length=1, max_length=240)
    raw_response_text: str | None = Field(default=None, min_length=1, max_length=4096)
    raw_response_hash: str | None = Field(default=None, min_length=64, max_length=64)
    provider_name: str = Field(min_length=1)
    provider_version: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    output_schema_version: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime
    latency_ms: int = Field(ge=0)
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("DECISION_FINISHED_BEFORE_STARTED")
        if self.status is DecisionEvaluationStatus.AVAILABLE:
            if (
                self.action is DecisionAction.NO_SIGNAL
                or self.thesis is None
                or self.invalidation is None
                or self.raw_response_text is None
                or self.raw_response_hash is None
                or self.error_code is not None
            ):
                raise ValueError("DECISION_AVAILABLE_PAYLOAD_INVALID")
        elif self.status is DecisionEvaluationStatus.IN_PROGRESS:
            if (
                self.action is not DecisionAction.NO_SIGNAL
                or self.thesis is not None
                or self.invalidation is not None
                or self.raw_response_text is not None
                or self.raw_response_hash is not None
                or self.error_code is not None
            ):
                raise ValueError("DECISION_IN_PROGRESS_PAYLOAD_FORBIDDEN")
        elif (
            self.action is not DecisionAction.NO_SIGNAL
            or self.thesis is not None
            or self.invalidation is not None
            or self.raw_response_text is not None
        ):
            raise ValueError("DECISION_FAILED_MODEL_PAYLOAD_FORBIDDEN")
        elif not self.error_code:
            raise ValueError("DECISION_FAILED_ERROR_REQUIRED")
        return self
