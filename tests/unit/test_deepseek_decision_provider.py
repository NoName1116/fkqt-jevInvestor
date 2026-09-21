from types import SimpleNamespace
from typing import Any

import pytest

from fkqt_jevinvestor.domain.decision import (
    DecisionAction,
    DecisionEvaluationCommand,
)
from fkqt_jevinvestor.providers.base import (
    ProviderContractError,
    ProviderUnavailableError,
)
from fkqt_jevinvestor.providers.deepseek_decision import DeepSeekDecisionProvider
from tests.unit.test_decision_contracts import _input


class FakeResponses:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response: object) -> None:
        self.responses = FakeResponses(response)


class FailingResponses:
    async def create(self, **kwargs: Any) -> object:
        del kwargs
        raise ConnectionError("secret upstream body deepseek-secret-value")


class FailingClient:
    def __init__(self) -> None:
        self.responses = FailingResponses()


def _command() -> DecisionEvaluationCommand:
    return DecisionEvaluationCommand(
        decision_input=_input(),
        provider_name="deepseek",
        provider_version="responses-v1",
        model_id="deepseek-flash",
    )


def _response(
    text: object = '{"action":"ENTER","thesis":"证据一致。","invalidation":"趋势失效。"}',
    *,
    status: str = "completed",
    refusal: str | None = None,
) -> object:
    return SimpleNamespace(
        status=status,
        output_text=text,
        refusal=refusal,
        incomplete_details=None,
    )


@pytest.mark.asyncio
async def test_provider_sends_v41_schema_request_and_returns_validated_result() -> None:
    client = FakeClient(_response())
    provider = DeepSeekDecisionProvider(
        client=client,
        model="deepseek-flash",
        provider_version="responses-v1",
        reasoning_effort="high",
        timeout_seconds=30,
    )

    result = await provider.evaluate(_command())

    call = client.responses.calls[0]
    assert call["model"] == "deepseek-flash"
    assert call["reasoning"] == {"effort": "high"}
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["name"] == "decision_output_v1"
    assert call["timeout"] == 30
    assert result.output.action == "ENTER"
    assert result.raw_response_hash and len(result.raw_response_hash) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error_code"),
    [
        (_response(""), "DECISION_RESPONSE_EMPTY"),
        (_response("x" * 4097), "DECISION_RESPONSE_TOO_LONG"),
        (_response(refusal="not allowed"), "DECISION_RESPONSE_REFUSED"),
        (_response(status="incomplete"), "DECISION_RESPONSE_INCOMPLETE"),
        (_response("not-json"), "DECISION_RESPONSE_JSON_INVALID"),
        (
            _response(
                '{"action":"ENTER","thesis":"证据一致。",'
                '"invalidation":"趋势失效。","confidence":0.9}'
            ),
            "DECISION_RESPONSE_SCHEMA_INVALID",
        ),
        (
            _response(
                '{"action":"KEEP","thesis":"证据一致。",'
                '"invalidation":"趋势失效。"}'
            ),
            "DECISION_ACTION_NOT_ALLOWED",
        ),
    ],
)
async def test_provider_rejects_invalid_response_with_only_hash(
    response: object,
    error_code: str,
) -> None:
    provider = DeepSeekDecisionProvider(
        client=FakeClient(response),
        model="deepseek-flash",
        provider_version="responses-v1",
        reasoning_effort="high",
        timeout_seconds=30,
    )

    with pytest.raises(ProviderContractError, match=error_code) as captured:
        await provider.evaluate(_command())

    if error_code not in {"DECISION_RESPONSE_REFUSED", "DECISION_RESPONSE_INCOMPLETE"}:
        assert captured.value.response_hash is not None
    assert "confidence" not in str(captured.value)
    assert "not allowed" not in str(captured.value)


@pytest.mark.asyncio
async def test_provider_hides_sdk_error_details_and_secret() -> None:
    provider = DeepSeekDecisionProvider(
        client=FailingClient(),
        model="deepseek-flash",
        provider_version="responses-v1",
        reasoning_effort="high",
        timeout_seconds=30,
    )

    with pytest.raises(ProviderUnavailableError, match="ConnectionError") as captured:
        await provider.evaluate(_command())

    assert "secret upstream body" not in str(captured.value)
    assert "deepseek-secret-value" not in str(captured.value)


@pytest.mark.asyncio
async def test_provider_rejects_command_config_mismatch_without_calling_sdk() -> None:
    client = FakeClient(_response())
    provider = DeepSeekDecisionProvider(
        client=client,
        model="deepseek-v4-pro",
        provider_version="responses-v1",
        reasoning_effort="high",
        timeout_seconds=30,
    )

    with pytest.raises(ProviderContractError, match="DECISION_PROVIDER_CONFIG_MISMATCH"):
        await provider.evaluate(_command())

    assert client.responses.calls == []
