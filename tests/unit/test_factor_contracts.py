from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fkqt_jevinvestor.domain.enums import FactorCode, FactorValueType, ProviderStatus
from fkqt_jevinvestor.domain.factors import (
    SemanticFactorBatch,
    SemanticFactorSnapshot,
    SemanticFactorValue,
)


def make_value(probabilities: dict[str, float]) -> SemanticFactorValue:
    return SemanticFactorValue(
        factor_code=FactorCode.EVENT_DIRECTION,
        value_type=FactorValueType.CHOICE,
        selected_label="POSITIVE",
        probabilities=probabilities,
        confidence=0.8,
    )


def make_snapshot(probabilities: dict[str, float]) -> SemanticFactorSnapshot:
    return SemanticFactorSnapshot(
        snapshot_id="factor-snapshot-1",
        symbol="600000.SH",
        as_of=datetime(2026, 9, 20, 15, tzinfo=UTC),
        provider="typesafe",
        model="jev-latest",
        question_version="semantic-factors-v1",
        input_hash="a" * 64,
        evidence_ids=("evidence-1",),
        values=(make_value(probabilities),),
        created_at=datetime(2026, 9, 20, 15, 1, tzinfo=UTC),
    )


def test_factor_codes_are_complete() -> None:
    assert {item.value for item in FactorCode} == {
        "SYMBOL_RELEVANCE",
        "EVENT_DIRECTION",
        "MATERIALITY",
        "NOVELTY",
        "PERSISTENCE",
        "THESIS_BREAK_RISK",
        "UNCERTAINTY",
        "INDUSTRY_SPILLOVER",
    }


def test_choice_distribution_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="sum to 1"):
        make_value({"POSITIVE": 0.8, "NEUTRAL": 0.3, "NEGATIVE": 0.1})


def test_confidence_must_be_between_zero_and_one() -> None:
    with pytest.raises(ValidationError):
        SemanticFactorValue(
            factor_code=FactorCode.EVENT_DIRECTION,
            value_type=FactorValueType.CHOICE,
            selected_label="POSITIVE",
            probabilities={"POSITIVE": 1.0},
            confidence=1.1,
        )


def test_snapshot_hash_is_stable_for_probability_key_order() -> None:
    first = make_snapshot({"POSITIVE": 0.7, "NEUTRAL": 0.2, "NEGATIVE": 0.1})
    second = make_snapshot({"NEGATIVE": 0.1, "POSITIVE": 0.7, "NEUTRAL": 0.2})

    assert first.content_hash() == second.content_hash()


def test_batch_exposes_auditable_status() -> None:
    batch = SemanticFactorBatch(
        request_id="request-1",
        status=ProviderStatus.AVAILABLE,
        snapshots=(make_snapshot({"POSITIVE": 1.0}),),
        latency_ms=12,
        error_code=None,
    )

    assert batch.status is ProviderStatus.AVAILABLE
    assert batch.snapshots[0].symbol == "600000.SH"
