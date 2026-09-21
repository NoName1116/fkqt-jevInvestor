import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import SecretStr, ValidationError
from typesafe_sdk import AsyncTypeSafeClient

from fkqt_jevinvestor.domain.enums import FactorCode, FactorValueType, ProviderStatus
from fkqt_jevinvestor.domain.factors import (
    SemanticFactorBatch,
    SemanticFactorSnapshot,
    SemanticFactorValue,
)
from fkqt_jevinvestor.providers.base import (
    ProviderContractError,
    ProviderHealth,
    ProviderUnavailableError,
    SemanticFactorRequest,
)
from fkqt_jevinvestor.providers.jev_questions import build_jev_questions


class SystemOneClient(Protocol):
    async def system_one(
        self,
        state: dict[str, Any],
        questions: dict[str, Any],
        *,
        model: str,
    ) -> Any:
        pass


QUESTION_CODES = {
    "symbol_relevance": FactorCode.SYMBOL_RELEVANCE,
    "event_direction": FactorCode.EVENT_DIRECTION,
    "materiality": FactorCode.MATERIALITY,
    "novelty": FactorCode.NOVELTY,
    "persistence": FactorCode.PERSISTENCE,
    "thesis_break_risk": FactorCode.THESIS_BREAK_RISK,
    "uncertainty": FactorCode.UNCERTAINTY,
    "industry_spillover": FactorCode.INDUSTRY_SPILLOVER,
}


class JevSemanticFactorProvider:
    def __init__(self, client: SystemOneClient, model: str) -> None:
        self._client = client
        self._model = model

    @classmethod
    def from_api_key(cls, api_key: SecretStr, model: str) -> "JevSemanticFactorProvider":
        client = AsyncTypeSafeClient(api_key=api_key.get_secret_value(), model=model)
        return cls(client=client, model=model)

    async def evaluate(self, request: SemanticFactorRequest) -> SemanticFactorBatch:
        started = time.perf_counter()
        state = self._state(request)
        input_hash = self._hash(state)
        try:
            response = await self._client.system_one(
                state=state,
                questions=build_jev_questions(),
                model=self._model,
            )
            values = self._values(response)
        except ProviderContractError:
            raise
        except (ValidationError, ValueError, KeyError, AttributeError) as exc:
            raise ProviderContractError(str(exc)) from exc
        except Exception as exc:
            raise ProviderUnavailableError(type(exc).__name__) from exc

        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        snapshot = SemanticFactorSnapshot(
            snapshot_id=f"jev-{input_hash[:24]}",
            symbol=request.symbol,
            as_of=request.as_of,
            provider="typesafe",
            model=str(response.model),
            question_version="semantic-factors-v1",
            input_hash=input_hash,
            evidence_ids=tuple(item.evidence_id for item in request.evidence),
            values=values,
            created_at=datetime.now(UTC),
        )
        return SemanticFactorBatch(
            request_id=f"request-{input_hash[:24]}",
            status=ProviderStatus.AVAILABLE,
            snapshots=(snapshot,),
            latency_ms=latency_ms,
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider="jev",
            status=ProviderStatus.AVAILABLE,
            checked_at=datetime.now(UTC),
        )

    @staticmethod
    def _state(request: SemanticFactorRequest) -> dict[str, Any]:
        return {
            "symbol": request.symbol,
            "company": request.company,
            "industry": request.industry,
            "prior_context": request.prior_context,
            "current_thesis": request.current_thesis,
            "event": [
                {
                    "evidence_id": item.evidence_id,
                    "title": item.title,
                    "excerpt": item.evidence_excerpt,
                    "published_at": item.published_at.isoformat(),
                }
                for item in request.evidence
            ],
        }

    @staticmethod
    def _hash(state: dict[str, Any]) -> str:
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _values(response: Any) -> tuple[SemanticFactorValue, ...]:
        values: list[SemanticFactorValue] = []
        for question_id, answer in response.choices.items():
            if answer.choice not in answer.probabilities:
                raise ProviderContractError(f"unknown choice label: {answer.choice}")
            values.append(
                SemanticFactorValue(
                    factor_code=QUESTION_CODES[question_id],
                    value_type=FactorValueType.CHOICE,
                    selected_label=str(answer.choice),
                    probabilities={str(key): float(value) for key, value in answer.probabilities.items()},
                    confidence=float(answer.confidence),
                )
            )
        for question_id, answer in response.scores.items():
            values.append(
                SemanticFactorValue(
                    factor_code=QUESTION_CODES[question_id],
                    value_type=FactorValueType.SCORE,
                    score=float(answer.score),
                    probabilities={str(key): float(value) for key, value in answer.probabilities.items()},
                    confidence=float(answer.confidence),
                )
            )
        if {value.factor_code for value in values} != set(QUESTION_CODES.values()):
            raise ProviderContractError("Jev response does not contain all semantic factors")
        return tuple(sorted(values, key=lambda item: item.factor_code.value))
