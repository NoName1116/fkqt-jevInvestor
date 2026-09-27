from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from fkqt_jevinvestor.domain.decision import (
    DecisionAction,
    DecisionEvaluationCommand,
    DecisionEvaluationStatus,
    DecisionEvaluationV1,
    DecisionInputV1,
    DecisionMembership,
    DecisionModelOutputV1,
)
from fkqt_jevinvestor.domain.jev_market import (
    JevEvaluationStatus,
    JevEvaluationV1,
    JevQuestionResultV1,
    JevScope,
)
from fkqt_jevinvestor.domain.market_features import FeatureValue, MarketFeatureSnapshot
from fkqt_jevinvestor.domain.portfolio import PortfolioState
from fkqt_jevinvestor.providers.jev_market_questions import QUESTION_DEFINITIONS


def _jev(
    scope: JevScope,
    *,
    symbol: str | None,
    finished_at: datetime,
) -> JevEvaluationV1:
    results: list[JevQuestionResultV1] = []
    for definition in QUESTION_DEFINITIONS.values():
        if definition.scope is not scope:
            continue
        distribution = {label: Decimal(0) for label in definition.label_order}
        distribution[definition.label_order[0]] = Decimal(1)
        results.append(
            JevQuestionResultV1(
                question_id=definition.question_id,
                question_version=definition.question_version,
                criteria_version=definition.criteria_version,
                label_order=definition.label_order,
                distribution=distribution,
                selected_label=definition.label_order[0],
            )
        )
    return JevEvaluationV1(
        evaluation_id=f"jev-{scope.value.lower()}",
        formal_key="a" * 64,
        scope=scope,
        symbol=symbol,
        status=JevEvaluationStatus.AVAILABLE,
        results=tuple(results),
        provider_name="typesafe",
        provider_version="0.7.0",
        model_id="jev-market",
        state_schema_version="jev-state-v1",
        question_set_version="jev-pnl-questions-v1",
        input_hash="b" * 64,
        raw_response_hash="c" * 64,
        started_at=finished_at,
        finished_at=finished_at,
        latency_ms=1,
        error_code=None,
    )


def _input(**updates: object) -> DecisionInputV1:
    cutoff = datetime(2026, 9, 18, 15, tzinfo=UTC)
    feature = FeatureValue(
        feature_code="realized_vol_20d",
        feature_version="market-features-v1",
        as_of=cutoff,
        lookback_window=20,
        value=Decimal("0.02000000"),
        missing_reason=None,
        source_snapshot_hash="d" * 64,
    )
    payload: dict[str, object] = {
        "decision_date": date(2026, 9, 18),
        "decision_cutoff": cutoff,
        "planned_execution_date": date(2026, 9, 21),
        "candidate_universe_id": "universe-v1",
        "candidate_universe_hash": "e" * 64,
        "market_snapshot_hash": "f" * 64,
        "feature_snapshot": MarketFeatureSnapshot(
            symbol="600000.SH",
            decision_date=date(2026, 9, 18),
            values={"realized_vol_20d": feature},
            content_hash="1" * 64,
        ),
        "symbol": "600000.SH",
        "membership": DecisionMembership.CANDIDATE,
        "universe_jev": _jev(JevScope.UNIVERSE, symbol=None, finished_at=cutoff),
        "symbol_jev": _jev(
            JevScope.SYMBOL, symbol="600000.SH", finished_at=cutoff
        ),
        "portfolio": PortfolioState(
            portfolio_id="paper-main",
            cash_balance=Decimal(1000000),
            frozen_cash=Decimal(0),
            realized_pnl=Decimal(0),
            version=1,
        ),
        "total_equity": Decimal(1000000),
        "position": None,
        "recent_actions": (),
        "pending_orders": (),
        "allowed_actions": (DecisionAction.ENTER, DecisionAction.AVOID),
    }
    payload.update(updates)
    return DecisionInputV1.model_validate(payload)


def test_model_output_forbids_numeric_trade_fields() -> None:
    with pytest.raises(ValidationError):
        DecisionModelOutputV1.model_validate(
            {
                "action": "ENTER",
                "thesis": "输入证据一致。",
                "invalidation": "输入证据失效。",
                "target_position_pct": "0.1",
            }
        )


@pytest.mark.parametrize("action", ["NO_SIGNAL", "HOLD", "BUY"])
def test_model_output_rejects_non_model_actions(action: str) -> None:
    with pytest.raises(ValidationError):
        DecisionModelOutputV1(
            action=action,  # type: ignore[arg-type]
            thesis="输入证据一致。",
            invalidation="输入证据失效。",
        )


def test_decision_input_rejects_action_outside_position_state() -> None:
    with pytest.raises(ValidationError, match="DECISION_ALLOWED_ACTIONS_INVALID"):
        _input(allowed_actions=(DecisionAction.KEEP, DecisionAction.EXIT))


def test_held_only_input_cannot_enter() -> None:
    with pytest.raises(ValidationError, match="DECISION_HELD_ONLY_POSITION_REQUIRED"):
        _input(membership=DecisionMembership.HELD_ONLY)


def test_decision_input_rejects_future_feature_data() -> None:
    cutoff = datetime(2026, 9, 18, 15, tzinfo=UTC)
    future = FeatureValue(
        feature_code="realized_vol_20d",
        feature_version="market-features-v1",
        as_of=cutoff + timedelta(seconds=1),
        lookback_window=20,
        value=Decimal("0.02000000"),
        missing_reason=None,
        source_snapshot_hash="d" * 64,
    )
    updates: dict[str, object] = {
        "feature_snapshot": MarketFeatureSnapshot(
            symbol="600000.SH",
            decision_date=date(2026, 9, 18),
            values={"realized_vol_20d": future},
            content_hash="1" * 64,
        )
    }
    with pytest.raises(ValidationError, match="DECISION_POINT_IN_TIME_VIOLATION"):
        _input(**updates)


def test_decision_input_accepts_jev_computed_after_cutoff() -> None:
    cutoff = datetime(2026, 9, 18, 15, tzinfo=UTC)
    future = cutoff + timedelta(seconds=1)

    decision_input = _input(
        universe_jev=_jev(JevScope.UNIVERSE, symbol=None, finished_at=future),
        symbol_jev=_jev(
            JevScope.SYMBOL,
            symbol="600000.SH",
            finished_at=future,
        ),
    )

    assert decision_input.universe_jev.finished_at == future


def test_command_hash_is_canonical_and_formal_key_excludes_run_identity() -> None:
    left = _input()
    assert left.feature_snapshot is not None
    right = _input(
        feature_snapshot=left.feature_snapshot.model_copy(
            update={"values": dict(reversed(tuple(left.feature_snapshot.values.items())))}
        )
    )
    left_command = DecisionEvaluationCommand(
        decision_input=left,
        provider_name="deepseek",
        provider_version="responses-v1",
        model_id="deepseek-flash",
    )
    right_command = DecisionEvaluationCommand(
        decision_input=right,
        provider_name="deepseek",
        provider_version="responses-v1",
        model_id="deepseek-flash",
    )

    assert left_command.input_hash == right_command.input_hash
    assert left_command.formal_key == right_command.formal_key


def test_formal_key_includes_normalized_base_url_and_reasoning_effort() -> None:
    command = DecisionEvaluationCommand(
        decision_input=_input(),
        provider_name="deepseek",
        provider_version="responses-v1",
        model_id="deepseek-flash",
        provider_base_url="https://api.deepseek.com/",
        reasoning_effort="high",
    )

    assert command.provider_base_url == "https://api.deepseek.com"
    assert command.formal_key != command.model_copy(
        update={"reasoning_effort": "low"}
    ).formal_key
    assert command.formal_key != command.model_copy(
        update={"provider_base_url": "https://proxy.example.com"}
    ).formal_key


def test_evaluation_success_and_failure_payloads_are_mutually_exclusive() -> None:
    command = DecisionEvaluationCommand(
        decision_input=_input(),
        provider_name="deepseek",
        provider_version="responses-v1",
        model_id="deepseek-flash",
    )
    now = datetime(2026, 9, 18, 15, 0, 1, tzinfo=UTC)
    success = DecisionEvaluationV1(
        evaluation_id="decision-1",
        formal_key=command.formal_key,
        input_hash=command.input_hash,
        symbol="600000.SH",
        status=DecisionEvaluationStatus.AVAILABLE,
        action=DecisionAction.ENTER,
        thesis="输入证据一致。",
        invalidation="输入证据失效。",
        raw_response_text='{"action":"ENTER"}',
        raw_response_hash="2" * 64,
        provider_name="deepseek",
        provider_version="responses-v1",
        model_id="deepseek-flash",
        prompt_version=command.prompt_version,
        output_schema_version=command.output_schema_version,
        started_at=now,
        finished_at=now,
        latency_ms=1,
    )
    assert success.action is DecisionAction.ENTER

    with pytest.raises(ValidationError, match="DECISION_FAILED_MODEL_PAYLOAD_FORBIDDEN"):
        DecisionEvaluationV1(
            evaluation_id="decision-2",
            formal_key=command.formal_key,
            input_hash=command.input_hash,
            symbol="600000.SH",
            status=DecisionEvaluationStatus.CONTRACT_INVALID,
            action=DecisionAction.NO_SIGNAL,
            thesis="不应保存。",
            invalidation=None,
            raw_response_text=None,
            raw_response_hash="3" * 64,
            provider_name="deepseek",
            provider_version="responses-v1",
            model_id="deepseek-flash",
            prompt_version=command.prompt_version,
            output_schema_version=command.output_schema_version,
            started_at=now,
            finished_at=now,
            latency_ms=1,
            error_code="DECISION_OUTPUT_INVALID",
        )
