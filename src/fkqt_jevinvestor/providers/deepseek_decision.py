import hashlib
import json
from typing import Any, Literal, Protocol, cast

from pydantic import SecretStr, ValidationError

from fkqt_jevinvestor.domain.decision import (
    DecisionAction,
    DecisionEvaluationCommand,
    DecisionModelOutputV1,
    DecisionProviderResult,
)
from fkqt_jevinvestor.providers.base import (
    ProviderContractError,
    ProviderUnavailableError,
)
from fkqt_jevinvestor.providers.decision_prompt import (
    DECISION_MAX_OUTPUT_CHARS,
    build_decision_messages,
    decision_output_json_schema,
)


class ResponsesResource(Protocol):
    async def create(self, **kwargs: Any) -> object:
        ...


class ResponsesClient(Protocol):
    @property
    def responses(self) -> ResponsesResource: ...


class DeepSeekDecisionProvider:
    def __init__(
        self,
        *,
        client: ResponsesClient,
        model: str,
        provider_version: str,
        reasoning_effort: Literal["low", "high"],
        timeout_seconds: float,
        base_url: str = "https://api.deepseek.com",
    ) -> None:
        self._client = client
        self._model = model
        self._provider_version = provider_version
        self._reasoning_effort = reasoning_effort
        self._timeout_seconds = timeout_seconds
        self._base_url = base_url.rstrip("/")

    @classmethod
    def from_api_key(
        cls,
        *,
        api_key: SecretStr,
        base_url: str,
        model: str,
        provider_version: str,
        reasoning_effort: Literal["low", "high"],
        timeout_seconds: float,
    ) -> "DeepSeekDecisionProvider":
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )
        return cls(
            client=cast(ResponsesClient, client),
            model=model,
            provider_version=provider_version,
            reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds,
            base_url=base_url,
        )

    async def evaluate(
        self,
        command: DecisionEvaluationCommand,
    ) -> DecisionProviderResult:
        try:
            command = DecisionEvaluationCommand.model_validate(command.model_dump())
        except ValidationError as exc:
            raise ProviderContractError("DECISION_INPUT_CONTRACT_INVALID") from exc
        self._validate_command(command)
        try:
            response = await self._client.responses.create(
                model=self._model,
                input=[dict(message) for message in build_decision_messages(command.decision_input)],
                reasoning={"effort": self._reasoning_effort},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "decision_output_v1",
                        "strict": True,
                        "schema": decision_output_json_schema(),
                    }
                },
                timeout=self._timeout_seconds,
            )
        except Exception as exc:
            raise ProviderUnavailableError(type(exc).__name__) from exc
        return self._parse_response(response, command)

    def _validate_command(self, command: DecisionEvaluationCommand) -> None:
        if (
            command.provider_name != "deepseek"
            or command.provider_version != self._provider_version
            or command.model_id != self._model
            or command.provider_base_url.rstrip("/") != self._base_url
            or command.reasoning_effort != self._reasoning_effort
        ):
            raise ProviderContractError("DECISION_PROVIDER_CONFIG_MISMATCH")

    @staticmethod
    def _parse_response(
        response: object,
        command: DecisionEvaluationCommand,
    ) -> DecisionProviderResult:
        nested_refusal = any(
            getattr(content, "type", None) == "refusal"
            or bool(getattr(content, "refusal", None))
            for output in (getattr(response, "output", None) or ())
            for content in (getattr(output, "content", None) or ())
        )
        if getattr(response, "refusal", None) or nested_refusal:
            raise ProviderContractError("DECISION_RESPONSE_REFUSED")
        if getattr(response, "status", None) != "completed":
            raise ProviderContractError("DECISION_RESPONSE_INCOMPLETE")
        raw_text = getattr(response, "output_text", None)
        if not isinstance(raw_text, str) or not raw_text:
            response_hash = hashlib.sha256(str(raw_text).encode("utf-8")).hexdigest()
            raise ProviderContractError(
                "DECISION_RESPONSE_EMPTY",
                response_hash=response_hash,
            )
        response_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        if len(raw_text) > DECISION_MAX_OUTPUT_CHARS:
            raise ProviderContractError(
                "DECISION_RESPONSE_TOO_LONG",
                response_hash=response_hash,
            )
        try:
            payload = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProviderContractError(
                "DECISION_RESPONSE_JSON_INVALID",
                response_hash=response_hash,
            ) from exc
        try:
            output = DecisionModelOutputV1.model_validate(payload)
        except ValidationError as exc:
            raise ProviderContractError(
                "DECISION_RESPONSE_SCHEMA_INVALID",
                response_hash=response_hash,
            ) from exc
        action = DecisionAction(output.action)
        if action not in command.decision_input.allowed_actions:
            raise ProviderContractError(
                "DECISION_ACTION_NOT_ALLOWED",
                response_hash=response_hash,
            )
        return DecisionProviderResult(
            output=output,
            raw_response_text=raw_text,
            raw_response_hash=response_hash,
        )
