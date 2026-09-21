import os

import pytest
from pydantic import SecretStr

from fkqt_jevinvestor.domain.decision import (
    DecisionAction,
    DecisionEvaluationCommand,
)
from fkqt_jevinvestor.providers.deepseek_decision import DeepSeekDecisionProvider
from tests.unit.test_decision_contracts import _input  # pyright: ignore[reportPrivateUsage]


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_deepseek_decision_contract_with_synthetic_empty_position() -> None:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if os.getenv("RUN_LIVE_DEEPSEEK_TESTS") != "1" or not api_key:
        pytest.skip(
            "set RUN_LIVE_DEEPSEEK_TESTS=1 and DEEPSEEK_API_KEY to run live tests"
        )

    model = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    provider_version = "deepseek-responses-live"
    command = DecisionEvaluationCommand(
        decision_input=_input(),
        provider_name="deepseek",
        provider_version=provider_version,
        model_id=model,
    )
    provider = DeepSeekDecisionProvider.from_api_key(
        api_key=SecretStr(api_key),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        model=model,
        provider_version=provider_version,
        reasoning_effort="high",
        timeout_seconds=30,
    )

    result = await provider.evaluate(command)

    assert DecisionAction(result.output.action) in {
        DecisionAction.ENTER,
        DecisionAction.AVOID,
    }
    assert set(result.output.model_dump()) == {"action", "thesis", "invalidation"}
    assert not {
        "target_position_pct",
        "quantity",
        "price",
        "confidence",
    }.intersection(result.output.model_dump())
