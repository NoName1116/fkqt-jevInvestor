from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from typesafe_sdk import Answer, ChoiceAnswer, SystemOneResponse, Usage

from fkqt_jevinvestor.domain.jev_market import (
    JevEvaluationCommand,
    JevEvaluationStatus,
    JevScope,
    JevStateHeaderV1,
    JevSymbolStateV1,
    JevUniverseStateV1,
)
from fkqt_jevinvestor.providers.base import (
    ProviderContractError,
    ProviderUnavailableError,
)
from fkqt_jevinvestor.providers.jev_market import TypeSafeJevMarketProvider
from fkqt_jevinvestor.providers.jev_market_questions import QUESTION_DEFINITIONS


class FakeClient:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def system_one(
        self,
        state: dict[str, Any],
        questions: dict[str, Any],
        *,
        model: str,
    ) -> Any:
        self.calls.append({"state": state, "questions": questions, "model": model})
        return self.response


class FailingClient:
    async def system_one(
        self,
        state: dict[str, Any],
        questions: dict[str, Any],
        *,
        model: str,
    ) -> Any:
        raise ConnectionError("secret upstream response body")


def _header() -> JevStateHeaderV1:
    return JevStateHeaderV1(
        decision_date=date(2026, 9, 18),
        decision_cutoff=datetime(2026, 9, 18, 15, tzinfo=UTC),
        candidate_universe_id="universe-v1",
        candidate_universe_hash="a" * 64,
        candidate_limit=20,
        candidate_actual_size=2,
        market_snapshot_hash="b" * 64,
        feature_set_version="market-features-v1",
    )


def _command(scope: JevScope = JevScope.SYMBOL) -> JevEvaluationCommand:
    if scope is JevScope.UNIVERSE:
        state = JevUniverseStateV1(
            header=_header(),
            metrics={"advance_ratio": Decimal("0.5")},
            coverage={"eligible_symbol_count": 2},
        )
    else:
        state = JevSymbolStateV1(
            header=_header(),
            symbol="600000.SH",
            security={"market": "SSE", "board": "MAIN"},
            features={"return_5d": Decimal("0.01")},
            missing_reasons=(),
        )
    return JevEvaluationCommand(
        scope=scope,
        state=state,
        provider_name="typesafe",
        provider_version="typesafe-sdk-test",
        model_id="jev-market-test",
    )


def _valid_response(scope: JevScope = JevScope.SYMBOL) -> Any:
    choices: dict[str, Any] = {}
    for definition in QUESTION_DEFINITIONS.values():
        if definition.scope is not scope:
            continue
        probability = Decimal(1) / Decimal(len(definition.label_order))
        distribution = {label: probability for label in definition.label_order}
        distribution[definition.label_order[0]] += Decimal("0.0000001")
        distribution[definition.label_order[-1]] -= Decimal("0.0000001")
        choices[definition.question_id] = SimpleNamespace(
            choice=definition.label_order[0],
            probabilities=distribution,
        )
    return SimpleNamespace(model="jev-market-test", choices=choices, scores={}, nouls={})


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [JevScope.UNIVERSE, JevScope.SYMBOL])
async def test_provider_sends_canonical_state_and_scope_questions(scope: JevScope) -> None:
    client = FakeClient(_valid_response(scope))
    provider = TypeSafeJevMarketProvider(
        client=client,
        model="jev-market-test",
        provider_version="typesafe-sdk-test",
    )

    evaluation = await provider.evaluate(_command(scope))

    expected_questions = {
        item.question_id for item in QUESTION_DEFINITIONS.values() if item.scope is scope
    }
    assert len(client.calls) == 1
    assert set(client.calls[0]["questions"]) == expected_questions
    assert client.calls[0]["state"] == _command(scope).state.model_dump(mode="json")
    assert client.calls[0]["model"] == "jev-market-test"
    assert evaluation.status == JevEvaluationStatus.AVAILABLE
    assert {item.question_id for item in evaluation.results} == expected_questions
    assert all(isinstance(value, Decimal) for item in evaluation.results for value in item.distribution.values())
    assert evaluation.raw_response_hash is not None


def _mutated_symbol_response(mutation: str) -> Any:
    response = _valid_response()
    if mutation == "missing_question":
        del response.choices["data_sufficiency"]
    elif mutation == "unknown_question":
        response.choices["unknown"] = response.choices["data_sufficiency"]
    else:
        answer = response.choices["profitability_5d"]
        if mutation == "selected_unknown":
            answer.choice = "SECRET_RAW_LABEL"
        elif mutation == "missing_label":
            del answer.probabilities["LOSS"]
        elif mutation == "unknown_label":
            answer.probabilities["SECRET_RAW_LABEL"] = answer.probabilities.pop("LOSS")
        elif mutation == "negative":
            answer.probabilities = {
                "PROFITABLE": Decimal("-0.1"),
                "FLAT": Decimal("0.5"),
                "LOSS": Decimal("0.6"),
            }
        elif mutation == "nan":
            answer.probabilities["PROFITABLE"] = Decimal("NaN")
        elif mutation == "infinity":
            answer.probabilities["PROFITABLE"] = Decimal("Infinity")
        elif mutation == "sum":
            answer.probabilities = {
                "PROFITABLE": Decimal("0.7"),
                "FLAT": Decimal("0.2"),
                "LOSS": Decimal("0.2"),
            }
        elif mutation == "wrong_highest":
            answer.choice = "LOSS"
    return response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "selected_unknown",
        "missing_question",
        "unknown_question",
        "missing_label",
        "unknown_label",
        "negative",
        "nan",
        "infinity",
        "sum",
        "wrong_highest",
    ],
)
async def test_provider_rejects_invalid_contract_with_only_response_hash(
    mutation: str,
) -> None:
    provider = TypeSafeJevMarketProvider(
        client=FakeClient(_mutated_symbol_response(mutation)),
        model="jev-market-test",
        provider_version="typesafe-sdk-test",
    )

    with pytest.raises(ProviderContractError) as captured:
        await provider.evaluate(_command())

    assert captured.value.response_hash is not None
    assert len(captured.value.response_hash) == 64
    assert "SECRET_RAW_LABEL" not in str(captured.value)
    assert "probabilities" not in str(captured.value)


@pytest.mark.asyncio
async def test_provider_hides_network_error_details() -> None:
    provider = TypeSafeJevMarketProvider(
        client=FailingClient(),
        model="jev-market-test",
        provider_version="typesafe-sdk-test",
    )

    with pytest.raises(ProviderUnavailableError, match="ConnectionError") as captured:
        await provider.evaluate(_command())

    assert "secret upstream response body" not in str(captured.value)


def test_provider_contract_error_remains_backward_compatible() -> None:
    legacy = ProviderContractError("legacy message")
    assert str(legacy) == "legacy message"
    assert legacy.response_hash is None


@pytest.mark.asyncio
async def test_provider_accepts_locked_sdk_response_models() -> None:
    answers: dict[str, Answer] = {}
    for definition in QUESTION_DEFINITIONS.values():
        if definition.scope is not JevScope.SYMBOL:
            continue
        probabilities = {label: 0.0 for label in definition.label_order}
        probabilities[definition.label_order[0]] = 1.0
        answers[definition.question_id] = ChoiceAnswer(
            choice=definition.label_order[0],
            confidence=1.0,
            probabilities=probabilities,
        )
    response = SystemOneResponse(
        model="jev-market-test",
        usage=Usage(input_tokens=10, output_tokens=5),
        answers=answers,
    )
    provider = TypeSafeJevMarketProvider(
        client=FakeClient(response),
        model="jev-market-test",
        provider_version="typesafe-sdk-test",
    )

    result = await provider.evaluate(_command())

    assert result.status == JevEvaluationStatus.AVAILABLE
    assert len(result.results) == 5


@pytest.mark.asyncio
async def test_provider_revalidates_state_before_external_call() -> None:
    command = _command()
    invalid_state = command.state.model_copy(
        update={"features": {"future_label": Decimal(1)}}
    )
    command = command.model_copy(update={"state": invalid_state})
    client = FakeClient(_valid_response())
    provider = TypeSafeJevMarketProvider(
        client=client,
        model="jev-market-test",
        provider_version="typesafe-sdk-test",
    )

    with pytest.raises(ProviderContractError, match="JEV_STATE_CONTRACT_INVALID"):
        await provider.evaluate(command)

    assert client.calls == []
