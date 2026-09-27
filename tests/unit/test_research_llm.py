from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from fkqt_jevinvestor.domain.backtest import ExperimentArm
from fkqt_jevinvestor.domain.decision import DecisionAction
from fkqt_jevinvestor.providers.base import ProviderContractError, ProviderUnavailableError
from fkqt_jevinvestor.research.llm import ResearchDeepSeekProvider
from fkqt_jevinvestor.research.models import ResearchLlmRequest, ResearchStrategyConfig
from tests.unit.test_deepseek_decision_provider import FailingClient, FakeClient


def request() -> ResearchLlmRequest:
    return ResearchLlmRequest(
        experiment_arm=ExperimentArm.B_LLM,
        symbol="000001.SZ",
        decision_date=date(2026, 9, 18),
        decision_cutoff=datetime(2026, 9, 18, 15, tzinfo=UTC),
        allowed_actions=(DecisionAction.ENTER, DecisionAction.AVOID),
        payload={"allowed_actions": ["ENTER", "AVOID"], "feature_snapshot": {}},
        model_config_snapshot=ResearchStrategyConfig(),
    )


async def test_research_provider_sends_schema_and_actual_usage_without_jev() -> None:
    response = SimpleNamespace(
        status="completed",
        output_text=('{"action":"ENTER","thesis":"冻结输入。","invalidation":"趋势改变。"}'),
        usage=SimpleNamespace(input_tokens=7, output_tokens=3, total_tokens=10),
    )
    client = FakeClient(response)
    result = await ResearchDeepSeekProvider(client, ResearchStrategyConfig()).evaluate(request())
    assert result.output.action == "ENTER"
    assert result.usage is not None and result.usage["input_tokens"] == 7
    call = client.responses.calls[0]
    assert call["reasoning"] == {"effort": "high"}
    assert call["text"]["format"]["strict"] is True
    assert "jev" not in call["input"][1]["content"].lower()


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "",
        "x" * 4097,
        '{"action":"KEEP","thesis":"冻结输入。","invalidation":"趋势改变。"}',
        '{"action":"ENTER","thesis":"冻结输入。","invalidation":"趋势改变。","weight":0.2}',
    ],
)
async def test_research_provider_rejects_invalid_actions_and_numerical_outputs(raw: str) -> None:
    client = FakeClient(SimpleNamespace(status="completed", output_text=raw))
    with pytest.raises(ProviderContractError, match="RESEARCH_LLM_RESPONSE_INVALID"):
        await ResearchDeepSeekProvider(client, ResearchStrategyConfig()).evaluate(request())


async def test_research_provider_hides_transport_details() -> None:
    with pytest.raises(ProviderUnavailableError) as captured:
        await ResearchDeepSeekProvider(FailingClient(), ResearchStrategyConfig()).evaluate(
            request()
        )
    assert str(captured.value) == "ConnectionError"
