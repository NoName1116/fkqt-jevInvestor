import hashlib
import json
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SignalAction(StrEnum):
    OPEN = "OPEN"
    ADD = "ADD"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"
    HOLD = "HOLD"
    AVOID = "AVOID"


class SignalBatchStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class FixtureSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    action: SignalAction
    target_position_pct: Decimal = Field(ge=0, le=1)
    confidence: Decimal = Field(ge=0, le=1)
    thesis: str = Field(min_length=1, max_length=1000)
    invalidation: str = Field(min_length=1, max_length=1000)
    factor_codes: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def reject_non_fixture_references(self) -> Self:
        if self.factor_codes:
            raise ValueError("factor_codes must be empty for Phase 1 fixtures")
        if self.evidence_ids:
            raise ValueError("evidence_ids must be empty for Phase 1 fixtures")
        return self


class FixtureSignalBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    portfolio_id: str
    decision_date: date
    planned_execution_date: date
    fixture_version: str
    candidate_symbols: tuple[str, ...]
    signals: tuple[FixtureSignal, ...]
    cash_target_pct: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def require_future_execution_date(self) -> Self:
        if self.planned_execution_date <= self.decision_date:
            raise ValueError("planned_execution_date must be after decision_date")
        return self

    def content_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ValidatedSignalBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch: FixtureSignalBatch
    status: SignalBatchStatus = SignalBatchStatus.ACCEPTED
    input_hash: str = Field(min_length=64, max_length=64)
