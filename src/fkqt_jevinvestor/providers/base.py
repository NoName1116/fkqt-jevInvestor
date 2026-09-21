from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from fkqt_jevinvestor.domain.enums import ProviderStatus
from fkqt_jevinvestor.domain.evidence import MinimalEvidenceSnapshot
from fkqt_jevinvestor.domain.factors import SemanticFactorBatch


class ProviderUnavailableError(RuntimeError):
    pass


class ProviderContractError(RuntimeError):
    pass


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
