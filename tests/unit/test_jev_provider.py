from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from fkqt_jevinvestor.domain.evidence import MinimalEvidenceSnapshot
from fkqt_jevinvestor.providers.base import (
    ProviderContractError,
    ProviderUnavailableError,
    SemanticFactorRequest,
)
from fkqt_jevinvestor.providers.jev import JevSemanticFactorProvider
from fkqt_jevinvestor.providers.jev_questions import EXPECTED_QUESTION_IDS


class FakeClient:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.call_count = 0
        self.last_questions: dict[str, Any] = {}

    async def system_one(
        self,
        state: dict[str, Any],
        questions: dict[str, Any],
        *,
        model: str,
    ) -> Any:
        self.call_count += 1
        self.last_questions = questions
        return self.response


class FailingClient:
    async def system_one(
        self,
        state: dict[str, Any],
        questions: dict[str, Any],
        *,
        model: str,
    ) -> Any:
        raise ConnectionError("secret upstream response")


def factor_request() -> SemanticFactorRequest:
    evidence = MinimalEvidenceSnapshot(
        evidence_id="evidence-1",
        provider="news-api",
        external_id="external-1",
        source_type="NEWS",
        symbol="600000.SH",
        title="订单增长",
        published_at=datetime(2026, 9, 20, 8, tzinfo=UTC),
        available_from=datetime(2026, 9, 20, 8, 1, tzinfo=UTC),
        retrieved_at=datetime(2026, 9, 20, 15, tzinfo=UTC),
        source_url="https://example.com/1",
        evidence_excerpt="公司披露新增重大订单。",
        content_hash="a" * 64,
    )
    return SemanticFactorRequest(
        symbol="600000.SH",
        as_of=datetime(2026, 9, 20, 15, tzinfo=UTC),
        evidence=(evidence,),
        company={"name": "测试公司"},
        prior_context="此前订单稳定",
        current_thesis="需求增长",
        industry="银行",
    )


def valid_response() -> Any:
    choices = {
        "event_direction": SimpleNamespace(
            choice="POSITIVE",
            probabilities={"POSITIVE": 0.8, "NEUTRAL": 0.1, "NEGATIVE": 0.1},
            confidence=0.8,
        ),
        "persistence": SimpleNamespace(
            choice="MEDIUM",
            probabilities={"INTRADAY": 0.1, "SHORT": 0.2, "MEDIUM": 0.6, "LONG": 0.1},
            confidence=0.6,
        ),
    }
    scores = {
        key: SimpleNamespace(
            score=2.0,
            probabilities={0: 0.1, 1: 0.2, 2: 0.6, 3: 0.1},
            confidence=0.6,
        )
        for key in EXPECTED_QUESTION_IDS - choices.keys()
    }
    return SimpleNamespace(model="jev-1.13.0", choices=choices, scores=scores, nouls={})


@pytest.mark.asyncio
async def test_jev_provider_sends_all_questions_in_one_call() -> None:
    client = FakeClient(valid_response())
    provider = JevSemanticFactorProvider(client=client, model="jev-latest")

    batch = await provider.evaluate(factor_request())

    assert client.call_count == 1
    assert set(client.last_questions) == EXPECTED_QUESTION_IDS
    assert len(batch.snapshots[0].values) == 8
    assert batch.snapshots[0].model == "jev-1.13.0"


@pytest.mark.asyncio
async def test_jev_provider_rejects_unknown_choice_label() -> None:
    response = valid_response()
    response.choices["event_direction"].choice = "UNKNOWN"
    provider = JevSemanticFactorProvider(client=FakeClient(response), model="jev-latest")

    with pytest.raises(ProviderContractError, match="UNKNOWN"):
        await provider.evaluate(factor_request())


@pytest.mark.asyncio
async def test_jev_provider_rejects_incomplete_factor_response() -> None:
    response = valid_response()
    del response.scores["uncertainty"]
    provider = JevSemanticFactorProvider(client=FakeClient(response), model="jev-latest")

    with pytest.raises(ProviderContractError, match="all semantic factors"):
        await provider.evaluate(factor_request())


@pytest.mark.asyncio
async def test_jev_provider_hides_sdk_error_details() -> None:
    provider = JevSemanticFactorProvider(client=FailingClient(), model="jev-latest")

    with pytest.raises(ProviderUnavailableError, match="ConnectionError") as captured:
        await provider.evaluate(factor_request())

    assert "secret upstream response" not in str(captured.value)
