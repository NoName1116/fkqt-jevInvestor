from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from fkqt_jevinvestor.domain.jev_market import (
    JEV_SYMBOL_FEATURE_FIELDS,
    JEV_SYMBOL_SECURITY_FIELDS,
    JEV_UNIVERSE_COVERAGE_FIELDS,
    JEV_UNIVERSE_METRIC_FIELDS,
    JevEvaluationCommand,
    JevEvaluationStatus,
    JevEvaluationV1,
    JevQuestionResultV1,
    JevScope,
    JevStateHeaderV1,
    JevSymbolStateV1,
    JevUniverseStateV1,
)


def _header(**updates: object) -> JevStateHeaderV1:
    values: dict[str, object] = {
        "decision_date": date(2026, 9, 18),
        "decision_cutoff": datetime(2026, 9, 18, 15, tzinfo=UTC),
        "candidate_universe_id": "universe-v1",
        "candidate_universe_hash": "a" * 64,
        "candidate_limit": 20,
        "candidate_actual_size": 12,
        "market_snapshot_hash": "b" * 64,
        "feature_set_version": "market-features-v1",
    }
    values.update(updates)
    return JevStateHeaderV1.model_validate(values)


def _result(**updates: object) -> JevQuestionResultV1:
    values: dict[str, object] = {
        "question_id": "profitability_5d",
        "question_version": "profitability-5d-v1",
        "criteria_version": "pnl-label-criteria-v1",
        "label_order": ("PROFITABLE", "FLAT", "LOSS"),
        "distribution": {
            "PROFITABLE": Decimal("0.580000"),
            "FLAT": Decimal("0.240000"),
            "LOSS": Decimal("0.180000"),
        },
        "selected_label": "PROFITABLE",
    }
    values.update(updates)
    return JevQuestionResultV1.model_validate(values)


def _symbol_results() -> tuple[JevQuestionResultV1, ...]:
    contracts = (
        (
            "next_session_pnl",
            "next-session-pnl-v1",
            "pnl-label-criteria-v1",
            ("PROFIT", "FLAT", "LOSS"),
        ),
        (
            "profitability_5d",
            "profitability-5d-v1",
            "pnl-label-criteria-v1",
            ("PROFITABLE", "FLAT", "LOSS"),
        ),
        (
            "drawdown_risk_5d",
            "drawdown-risk-5d-v1",
            "pnl-label-criteria-v1",
            ("LOW", "MEDIUM", "HIGH"),
        ),
        (
            "payoff_asymmetry_5d",
            "payoff-asymmetry-5d-v1",
            "pnl-label-criteria-v1",
            ("UPSIDE_DOMINANT", "BALANCED", "DOWNSIDE_DOMINANT"),
        ),
        (
            "data_sufficiency",
            "data-sufficiency-v1",
            "data-sufficiency-criteria-v1",
            ("SUFFICIENT", "LIMITED", "INSUFFICIENT"),
        ),
    )
    return tuple(
        JevQuestionResultV1(
            question_id=question_id,
            question_version=question_version,
            criteria_version=criteria_version,
            label_order=label_order,
            distribution={label_order[0]: Decimal(1), label_order[1]: Decimal(0), label_order[2]: Decimal(0)},
            selected_label=label_order[0],
        )
        for question_id, question_version, criteria_version, label_order in contracts
    )


def _evaluation(**updates: object) -> JevEvaluationV1:
    now = datetime(2026, 9, 18, 15, 0, 1, tzinfo=UTC)
    values: dict[str, object] = {
        "evaluation_id": "evaluation-1",
        "formal_key": "c" * 64,
        "scope": JevScope.SYMBOL,
        "symbol": "600000.SH",
        "status": JevEvaluationStatus.AVAILABLE,
        "results": _symbol_results(),
        "provider_name": "typesafe",
        "provider_version": "typesafe-sdk-0.7",
        "model_id": "jev-latest",
        "state_schema_version": "jev-state-v1",
        "question_set_version": "jev-pnl-questions-v1",
        "input_hash": "d" * 64,
        "raw_response_hash": "e" * 64,
        "started_at": now,
        "finished_at": now,
        "latency_ms": 0,
        "error_code": None,
    }
    values.update(updates)
    return JevEvaluationV1.model_validate(values)


def test_header_rejects_actual_size_above_configured_limit() -> None:
    with pytest.raises(ValueError, match="CANDIDATE_TARGET_EXCEEDED"):
        _header(candidate_limit=10, candidate_actual_size=11)


@pytest.mark.parametrize(
    ("distribution", "message"),
    [
        ({"PROFITABLE": Decimal("0.7"), "LOSS": Decimal("0.3")}, "LABELS"),
        (
            {
                "PROFITABLE": Decimal("0.6"),
                "FLAT": Decimal("0.2"),
                "LOSS": Decimal("0.1"),
                "UNKNOWN": Decimal("0.1"),
            },
            "LABELS",
        ),
        (
            {
                "PROFITABLE": Decimal("0.7"),
                "FLAT": Decimal("0.2"),
                "LOSS": Decimal("0.2"),
            },
            "SUM",
        ),
    ],
)
def test_question_result_rejects_invalid_distribution(
    distribution: dict[str, Decimal],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=f"JEV_PROBABILITY_{message}_INVALID"):
        _result(distribution=distribution)


@pytest.mark.parametrize("invalid", [Decimal("NaN"), Decimal("Infinity"), Decimal("-0.1")])
def test_question_result_rejects_non_finite_or_negative_probability(
    invalid: Decimal,
) -> None:
    distribution = {
        "PROFITABLE": invalid,
        "FLAT": Decimal("0.5"),
        "LOSS": Decimal("0.5"),
    }
    with pytest.raises(ValueError, match="JEV_PROBABILITY_VALUE_INVALID"):
        _result(distribution=distribution)


def test_question_result_uses_label_order_to_break_probability_tie() -> None:
    with pytest.raises(ValueError, match="JEV_SELECTED_LABEL_INVALID"):
        _result(
            distribution={
                "PROFITABLE": Decimal("0.4"),
                "FLAT": Decimal("0.4"),
                "LOSS": Decimal("0.2"),
            },
            selected_label="FLAT",
        )


def test_available_evaluation_requires_results_and_no_error() -> None:
    with pytest.raises(ValueError, match="JEV_AVAILABLE_RESULT_REQUIRED"):
        _evaluation(results=())
    with pytest.raises(ValueError, match="JEV_AVAILABLE_ERROR_FORBIDDEN"):
        _evaluation(error_code="UPSTREAM_ERROR")


def test_failed_evaluation_forbids_results_and_requires_error() -> None:
    with pytest.raises(ValueError, match="JEV_FAILED_RESULT_FORBIDDEN"):
        _evaluation(status=JevEvaluationStatus.CONTRACT_INVALID, error_code="BAD")
    with pytest.raises(ValueError, match="JEV_FAILED_ERROR_REQUIRED"):
        _evaluation(status=JevEvaluationStatus.PROVIDER_UNAVAILABLE, results=(), error_code=None)


def test_scope_and_symbol_must_match() -> None:
    with pytest.raises(ValueError, match="JEV_UNIVERSE_SYMBOL_FORBIDDEN"):
        _evaluation(scope=JevScope.UNIVERSE, symbol="600000.SH")
    with pytest.raises(ValueError, match="JEV_SYMBOL_REQUIRED"):
        _evaluation(scope=JevScope.SYMBOL, symbol=None)


def test_command_hash_is_canonical_and_excludes_run_identity() -> None:
    features = {code: Decimal("0.01") for code in JEV_SYMBOL_FEATURE_FIELDS}
    first = JevSymbolStateV1(
        header=_header(),
        symbol="600000.SH",
        security=_complete_security(available=len(features)),
        features=features,
        missing_reasons=(),
    )
    second = first.model_copy(
        update={
            "security": dict(reversed(tuple(first.security.items()))),
            "features": dict(reversed(tuple(first.features.items()))),
        }
    )
    first_command = JevEvaluationCommand(
        scope=JevScope.SYMBOL,
        state=first,
        provider_name="typesafe",
        provider_version="typesafe-sdk-0.7",
        model_id="jev-latest",
    )
    second_command = first_command.model_copy(update={"state": second})

    assert first_command.input_hash == second_command.input_hash
    assert first_command.formal_key == second_command.formal_key
    assert "run_id" not in first_command.state.model_dump()


def test_state_scope_must_match_command_scope() -> None:
    state = JevUniverseStateV1(
        header=_header(),
        metrics={code: Decimal(0) for code in JEV_UNIVERSE_METRIC_FIELDS},
        coverage={
            "eligible_symbol_count": 12,
            "missing_symbol_count": 0,
            "coverage_ratio": Decimal(1),
            "missing_reasons": (),
        },
    )
    with pytest.raises(ValueError, match="JEV_COMMAND_SCOPE_MISMATCH"):
        JevEvaluationCommand(
            scope=JevScope.SYMBOL,
            state=state,
            provider_name="typesafe",
            provider_version="typesafe-sdk-0.7",
            model_id="jev-latest",
        )


@pytest.mark.parametrize(
    ("field", "payload"),
    [
        ("features", {"future_label": Decimal(1)}),
        ("security", {"cash": "1000000"}),
    ],
)
def test_symbol_state_rejects_non_whitelisted_fields(
    field: str,
    payload: dict[str, Decimal | str],
) -> None:
    values: dict[str, object] = {
        "header": _header(),
        "symbol": "600000.SH",
        "security": {"market": "SSE"},
        "features": {"return_5d": Decimal("0.01")},
        "missing_reasons": (),
    }
    values[field] = payload
    with pytest.raises(ValueError, match="JEV_STATE_FIELD_FORBIDDEN"):
        JevSymbolStateV1.model_validate(values)


def test_available_symbol_evaluation_requires_complete_frozen_question_set() -> None:
    with pytest.raises(ValueError, match="JEV_RESULT_QUESTION_SET_INVALID"):
        _evaluation(results=(_symbol_results()[0],))

    wrong_version = _symbol_results()[0].model_copy(update={"question_version": "v2"})
    with pytest.raises(ValueError, match="JEV_RESULT_QUESTION_CONTRACT_INVALID"):
        _evaluation(results=(wrong_version, *_symbol_results()[1:]))


def _complete_security(*, available: int) -> dict[str, str | bool | int | None]:
    values: dict[str, str | bool | int | None] = {
        "market": "SSE",
        "board": "MAIN",
        "listing_age_trading_days": 1000,
        "trading_status": "TRADING",
        "is_st_or_delisting_risk": False,
        "is_initial_no_limit_period": False,
        "corporate_action_status": "NONE",
        "adjustment_mode": "QFQ",
        "available_feature_count": available,
        "required_feature_count": len(JEV_SYMBOL_FEATURE_FIELDS),
    }
    assert set(values) == JEV_SYMBOL_SECURITY_FIELDS
    return values


def test_symbol_state_requires_complete_security_and_exact_missing_reasons() -> None:
    features = {"return_5d": Decimal("0.01")}
    missing = tuple(
        f"{code}:INSUFFICIENT_HISTORY"
        for code in sorted(JEV_SYMBOL_FEATURE_FIELDS - set(features))
    )
    valid = {
        "header": _header(),
        "symbol": "600000.SH",
        "security": _complete_security(available=1),
        "features": features,
        "missing_reasons": missing,
    }
    JevSymbolStateV1.model_validate(valid)

    with pytest.raises(ValueError, match="JEV_STATE_SECURITY_FIELDS_INVALID"):
        JevSymbolStateV1.model_validate({**valid, "security": {"market": "SSE"}})
    with pytest.raises(ValueError, match="JEV_STATE_MISSING_REASONS_INVALID"):
        JevSymbolStateV1.model_validate({**valid, "missing_reasons": ()})
    with pytest.raises(ValueError, match="JEV_STATE_FEATURE_COUNT_INVALID"):
        JevSymbolStateV1.model_validate(
            {**valid, "security": _complete_security(available=2)}
        )


def test_universe_state_requires_complete_metric_and_coverage_contract() -> None:
    metrics = {code: Decimal(0) for code in JEV_UNIVERSE_METRIC_FIELDS}
    coverage: dict[str, int | Decimal | tuple[str, ...] | None] = {
        "eligible_symbol_count": 1,
        "missing_symbol_count": 0,
        "coverage_ratio": Decimal(1),
        "missing_reasons": (),
    }
    assert set(coverage) == JEV_UNIVERSE_COVERAGE_FIELDS
    JevUniverseStateV1(
        header=_header(candidate_actual_size=1), metrics=metrics, coverage=coverage
    )

    with pytest.raises(ValueError, match="JEV_STATE_METRIC_FIELDS_INVALID"):
        JevUniverseStateV1(
            header=_header(),
            metrics={"advance_ratio": Decimal(1)},
            coverage=coverage,
        )


@pytest.mark.parametrize(
    "coverage",
    [
        {
            "eligible_symbol_count": 99,
            "missing_symbol_count": -87,
            "coverage_ratio": Decimal(2),
            "missing_reasons": (),
        },
        {
            "eligible_symbol_count": 11,
            "missing_symbol_count": 0,
            "coverage_ratio": Decimal(1),
            "missing_reasons": (),
        },
        {
            "eligible_symbol_count": 11,
            "missing_symbol_count": 1,
            "coverage_ratio": Decimal("0.91666666"),
            "missing_reasons": (),
        },
    ],
)
def test_universe_state_rejects_inconsistent_coverage(
    coverage: dict[str, int | Decimal | tuple[str, ...] | None],
) -> None:
    with pytest.raises(ValueError, match="JEV_STATE_COVERAGE_VALUES_INVALID"):
        JevUniverseStateV1(
            header=_header(),
            metrics={code: Decimal(0) for code in JEV_UNIVERSE_METRIC_FIELDS},
            coverage=coverage,
        )


def test_empty_universe_requires_none_coverage_ratio() -> None:
    JevUniverseStateV1(
        header=_header(candidate_actual_size=0),
        metrics={code: None for code in JEV_UNIVERSE_METRIC_FIELDS},
        coverage={
            "eligible_symbol_count": 0,
            "missing_symbol_count": 0,
            "coverage_ratio": None,
            "missing_reasons": (),
        },
    )
