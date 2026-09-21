import time
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, cast

from pydantic import SecretStr, ValidationError
from typesafe_sdk import AsyncTypeSafeClient

from fkqt_jevinvestor.domain.enums import ProviderStatus
from fkqt_jevinvestor.domain.jev_market import (
    JevEvaluationCommand,
    JevEvaluationStatus,
    JevEvaluationV1,
    JevQuestionResultV1,
    JevScope,
    JevSymbolStateV1,
)
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.providers.base import (
    ProviderContractError,
    ProviderHealth,
    ProviderUnavailableError,
)
from fkqt_jevinvestor.providers.jev_market_questions import (
    QUESTION_DEFINITIONS,
    build_jev_market_questions,
)


class SystemOneMarketClient(Protocol):
    async def system_one(
        self,
        state: dict[str, Any],
        questions: dict[str, Any],
        *,
        model: str,
    ) -> Any:
        ...


def _sanitized_response(response: object) -> dict[str, object]:
    raw_choices = getattr(response, "choices", None)
    sanitized_choices: dict[str, object] = {}
    if isinstance(raw_choices, Mapping):
        choices = cast(Mapping[object, object], raw_choices)
        for question_id, answer in sorted(choices.items(), key=lambda item: str(item[0])):
            raw_probabilities = getattr(answer, "probabilities", None)
            sanitized_probabilities: dict[str, str] = {}
            if isinstance(raw_probabilities, Mapping):
                probabilities = cast(Mapping[object, object], raw_probabilities)
                sanitized_probabilities = {
                    str(label): str(value)
                    for label, value in sorted(
                        probabilities.items(), key=lambda item: str(item[0])
                    )
                }
            sanitized_choices[str(question_id)] = {
                "choice": str(getattr(answer, "choice", "<missing>")),
                "probabilities": sanitized_probabilities,
            }
    return {
        "model": str(getattr(response, "model", "<missing>")),
        "choices": sanitized_choices,
    }


class TypeSafeJevMarketProvider:
    def __init__(
        self,
        client: SystemOneMarketClient,
        model: str,
        provider_version: str,
    ) -> None:
        self._client = client
        self._model = model
        self._provider_version = provider_version

    @classmethod
    def from_api_key(
        cls,
        api_key: SecretStr,
        model: str,
        provider_version: str,
    ) -> "TypeSafeJevMarketProvider":
        client = AsyncTypeSafeClient(
            api_key=api_key.get_secret_value(),
            model=model,
        )
        return cls(
            client=client,
            model=model,
            provider_version=provider_version,
        )

    async def evaluate(self, command: JevEvaluationCommand) -> JevEvaluationV1:
        self._validate_command_config(command)
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        try:
            response = await self._client.system_one(
                state=command.state.model_dump(mode="json"),
                questions=build_jev_market_questions(command.scope),
                model=self._model,
            )
        except Exception as exc:
            raise ProviderUnavailableError(type(exc).__name__) from exc

        response_hash = sha256_json(_sanitized_response(response))
        try:
            results = self._results(response, command.scope)
        except ProviderContractError as exc:
            if exc.response_hash is not None:
                raise
            raise ProviderContractError(str(exc), response_hash=response_hash) from exc
        except (
            ValidationError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            InvalidOperation,
        ) as exc:
            raise ProviderContractError(
                "JEV_RESPONSE_CONTRACT_INVALID",
                response_hash=response_hash,
            ) from exc

        finished_at = datetime.now(UTC)
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        return JevEvaluationV1(
            evaluation_id=f"jev-{command.formal_key[:24]}",
            formal_key=command.formal_key,
            scope=command.scope,
            symbol=(
                command.state.symbol
                if isinstance(command.state, JevSymbolStateV1)
                else None
            ),
            status=JevEvaluationStatus.AVAILABLE,
            results=results,
            provider_name=command.provider_name,
            provider_version=command.provider_version,
            model_id=command.model_id,
            state_schema_version=command.state.header.state_schema_version,
            question_set_version=command.question_set_version,
            input_hash=command.input_hash,
            raw_response_hash=response_hash,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=latency_ms,
            error_code=None,
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider="typesafe",
            status=ProviderStatus.AVAILABLE,
            checked_at=datetime.now(UTC),
        )

    def _validate_command_config(self, command: JevEvaluationCommand) -> None:
        if (
            command.provider_name != "typesafe"
            or command.provider_version != self._provider_version
            or command.model_id != self._model
        ):
            raise ProviderContractError("JEV_PROVIDER_CONFIG_MISMATCH")

    @staticmethod
    def _results(response: object, scope: JevScope) -> tuple[JevQuestionResultV1, ...]:
        raw_choices = getattr(response, "choices", None)
        if not isinstance(raw_choices, Mapping):
            raise ProviderContractError("JEV_CHOICES_MAPPING_REQUIRED")
        choices = cast(Mapping[object, object], raw_choices)
        expected = tuple(
            definition
            for definition in QUESTION_DEFINITIONS.values()
            if definition.scope is scope
        )
        expected_ids = {definition.question_id for definition in expected}
        if set(choices) != expected_ids:
            raise ProviderContractError("JEV_QUESTION_SET_INVALID")

        results: list[JevQuestionResultV1] = []
        for definition in expected:
            answer = choices[definition.question_id]
            raw_probabilities = getattr(answer, "probabilities", None)
            if not isinstance(raw_probabilities, Mapping):
                raise ProviderContractError("JEV_PROBABILITIES_MAPPING_REQUIRED")
            probabilities = cast(Mapping[object, object], raw_probabilities)
            distribution = {
                str(label): Decimal(str(value))
                for label, value in probabilities.items()
            }
            results.append(
                JevQuestionResultV1(
                    question_id=definition.question_id,
                    question_version=definition.question_version,
                    criteria_version=definition.criteria_version,
                    label_order=definition.label_order,
                    distribution=distribution,
                    selected_label=str(getattr(answer, "choice", "<missing>")),
                )
            )
        return tuple(results)
