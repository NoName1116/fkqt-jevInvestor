from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from fkqt_jevinvestor.domain.enums import ProviderStatus
from fkqt_jevinvestor.domain.evidence import MinimalEvidenceSnapshot
from fkqt_jevinvestor.domain.factors import SemanticFactorBatch
from fkqt_jevinvestor.domain.jev_market import JevEvaluationCommand, JevEvaluationV1


class ProviderUnavailableError(RuntimeError):
    pass


class ProviderContractError(RuntimeError):
    def __init__(self, message: str, response_hash: str | None = None) -> None:
        super().__init__(message)
        self.response_hash = response_hash


class ProviderHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    status: ProviderStatus
    checked_at: datetime
    message: str | None = None


class SemanticSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbols: tuple[str, ...]
    published_after: datetime
    available_before: datetime


class SemanticFactorRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: datetime
    evidence: tuple[MinimalEvidenceSnapshot, ...]
    company: dict[str, Any]
    prior_context: str = ""
    current_thesis: str = ""
    industry: str = "UNKNOWN"


class SemanticSourceProvider(Protocol):
    async def search(
        self,
        request: SemanticSearchRequest,
    ) -> Sequence[MinimalEvidenceSnapshot]:
        ...

    async def health(self) -> ProviderHealth:
        ...


class SemanticFactorProvider(Protocol):
    async def evaluate(self, request: SemanticFactorRequest) -> SemanticFactorBatch:
        ...

    async def health(self) -> ProviderHealth:
        ...


class JevMarketProvider(Protocol):
    async def evaluate(self, command: JevEvaluationCommand) -> JevEvaluationV1:
        ...

    async def health(self) -> ProviderHealth:
        ...
