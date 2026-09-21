import hashlib
import json
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fkqt_jevinvestor.domain.enums import FactorCode, FactorValueType, ProviderStatus


class SemanticFactorValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    factor_code: FactorCode
    value_type: FactorValueType
    selected_label: str | None = None
    score: float | None = None
    noul: float | None = Field(default=None, ge=0, le=1)
    probabilities: dict[str, float] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if any(value < 0 or value > 1 for value in self.probabilities.values()):
            raise ValueError("probabilities must be between 0 and 1")
        if self.value_type in {FactorValueType.CHOICE, FactorValueType.SCORE}:
            if not self.probabilities:
                raise ValueError("choice and score factors require probabilities")
            if abs(sum(self.probabilities.values()) - 1.0) > 1e-6:
                raise ValueError("probabilities must sum to 1")
        if (
            self.value_type is FactorValueType.CHOICE
            and self.selected_label not in self.probabilities
        ):
            raise ValueError("selected_label must exist in probabilities")
        return self


class SemanticFactorSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    symbol: str
    as_of: datetime
    provider: str
    model: str
    question_version: str
    input_hash: str = Field(min_length=64, max_length=64)
    evidence_ids: tuple[str, ...]
    values: tuple[SemanticFactorValue, ...]
    created_at: datetime

    def content_hash(self) -> str:
        payload = self.model_dump(mode="json")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SemanticFactorBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    status: ProviderStatus
    snapshots: tuple[SemanticFactorSnapshot, ...]
    latency_ms: int = Field(ge=0)
    error_code: str | None = None
