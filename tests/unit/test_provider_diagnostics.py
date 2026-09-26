import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from fkqt_jevinvestor.domain.jev_market import JevScope
from fkqt_jevinvestor.providers.base import ProviderContractError
from fkqt_jevinvestor.providers.jev_market import TypeSafeJevMarketProvider
from tests.unit.test_jev_market_provider import FakeClient, _command, _valid_response


@pytest.mark.asyncio
async def test_contract_failure_keeps_safe_specific_diagnostic() -> None:
    command = _command(JevScope.SYMBOL)
    response = _valid_response()
    first = next(iter(response.choices.values()))
    first.probabilities = {k: Decimal("0.1") for k in first.probabilities}
    provider = TypeSafeJevMarketProvider(FakeClient(response), command.model_id, command.provider_version)
    with pytest.raises(ProviderContractError) as caught:
        await provider.evaluate(command)
    diagnostic = caught.value.diagnostic
    assert "JEV_PROBABILITY_SUM_INVALID" in diagnostic["reason_codes"]
    assert diagnostic["input_hash"] == command.input_hash
    assert len(json.dumps(diagnostic).encode()) <= 16384


def test_unknown_sdk_fields_are_not_saved() -> None:
    from fkqt_jevinvestor.providers.diagnostics import build_jev_diagnostic, safe_usage

    secret = "Bearer SECRET_PRIVATE_VALUE"
    response = SimpleNamespace(model=secret, choices={secret: SimpleNamespace(choice=secret, probabilities={secret: secret})}, usage=SimpleNamespace(input_tokens=12, output_tokens=4))
    diagnostic = build_jev_diagnostic(response, _command(JevScope.SYMBOL), ("JEV_QUESTION_SET_INVALID",))
    assert secret not in json.dumps(diagnostic)
    assert safe_usage(response) == {"input_tokens": 12, "output_tokens": 4}
    response.usage.input_tokens = -1
    assert safe_usage(response) is None


@pytest.mark.asyncio
async def test_success_audit_does_not_change_old_serialization() -> None:
    command = _command(JevScope.SYMBOL)
    response = _valid_response()
    response.usage = SimpleNamespace(input_tokens=100, output_tokens=10)
    result = await TypeSafeJevMarketProvider(FakeClient(response), command.model_id, command.provider_version).evaluate(command)
    assert result.diagnostic is not None
    assert result.usage == {"input_tokens": 100, "output_tokens": 10}
    assert "diagnostic" not in result.model_dump(mode="json")
    assert "usage" not in result.model_dump(mode="json")
