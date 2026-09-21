from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from fkqt_jevinvestor.domain.portfolio import PortfolioState, PositionState
from fkqt_jevinvestor.domain.signals import FixtureSignal, FixtureSignalBatch, SignalAction
from fkqt_jevinvestor.services.signal_validator import SignalValidationError, validate_signal_batch


def signal(
    symbol: str,
    action: SignalAction,
    target: str,
    *,
    factor_codes: tuple[str, ...] = (),
) -> FixtureSignal:
    return FixtureSignal(
        symbol=symbol,
        action=action,
        target_position_pct=Decimal(target),
        confidence=Decimal("0.80"),
        thesis="Phase 1 fixture",
        invalidation="Fixture invalidation",
        factor_codes=factor_codes,
        evidence_ids=(),
    )


def portfolio(*positions: PositionState) -> PortfolioState:
    return PortfolioState(
        portfolio_id="portfolio-1",
        cash_balance=Decimal("1000000.00"),
        frozen_cash=Decimal(0),
        realized_pnl=Decimal(0),
        positions=positions,
        version=1,
    )


def batch(*signals: FixtureSignal, cash: str) -> FixtureSignalBatch:
    return FixtureSignalBatch(
        portfolio_id="portfolio-1",
        decision_date=date(2026, 9, 20),
        planned_execution_date=date(2026, 9, 21),
        fixture_version="fixture-v1",
        candidate_symbols=tuple(item.symbol for item in signals),
        signals=signals,
        cash_target_pct=Decimal(cash),
    )


def held_position(target: str = "0.40") -> PositionState:
    return PositionState(
        symbol="600000.SH",
        quantity=4000,
        sellable_quantity=4000,
        average_cost=Decimal(10),
        total_cost=Decimal(40000),
        last_price=Decimal(10),
        current_position_pct=Decimal(target),
        unrealized_pnl=Decimal(0),
        holding_trading_days=5,
    )


def test_signal_batch_rejects_unbalanced_targets() -> None:
    candidate = batch(signal("600000.SH", SignalAction.OPEN, "0.30"), cash="0.60")

    with pytest.raises(SignalValidationError) as captured:
        validate_signal_batch(candidate, portfolio(), {"600000.SH"})

    assert captured.value.codes == ("TARGET_WEIGHT_SUM_INVALID",)


@pytest.mark.parametrize(
    ("item", "state", "code"),
    [
        (signal("600000.SH", SignalAction.OPEN, "0.20"), portfolio(held_position()), "OPEN_REQUIRES_EMPTY_POSITION"),
        (signal("600000.SH", SignalAction.ADD, "0.30"), portfolio(held_position()), "ADD_MUST_INCREASE_POSITION"),
        (signal("600000.SH", SignalAction.REDUCE, "0.50"), portfolio(held_position()), "REDUCE_MUST_DECREASE_POSITION"),
        (signal("600000.SH", SignalAction.CLOSE, "0.10"), portfolio(held_position()), "CLOSE_REQUIRES_ZERO_TARGET"),
        (signal("600000.SH", SignalAction.HOLD, "0.30"), portfolio(held_position()), "HOLD_MUST_KEEP_POSITION"),
        (signal("600000.SH", SignalAction.AVOID, "0.10"), portfolio(), "AVOID_REQUIRES_ZERO_TARGET"),
    ],
)
def test_action_must_match_current_position(
    item: FixtureSignal,
    state: PortfolioState,
    code: str,
) -> None:
    candidate = batch(item, cash=str(Decimal(1) - item.target_position_pct))

    with pytest.raises(SignalValidationError) as captured:
        validate_signal_batch(candidate, state, {"600000.SH"})

    assert code in captured.value.codes


def test_signal_batch_rejects_duplicates_and_unknown_symbols() -> None:
    candidate = batch(
        signal("000001.SZ", SignalAction.OPEN, "0.20"),
        signal("000001.SZ", SignalAction.OPEN, "0.20"),
        cash="0.60",
    )

    with pytest.raises(SignalValidationError) as captured:
        validate_signal_batch(candidate, portfolio(), {"600000.SH"})

    assert captured.value.codes == (
        "DUPLICATE_SYMBOL",
        "SIGNAL_COVERAGE_INCOMPLETE",
        "SYMBOL_NOT_ALLOWED",
    )


def test_fixture_rejects_factor_references() -> None:
    with pytest.raises(ValidationError, match="factor_codes"):
        signal(
            "600000.SH",
            SignalAction.OPEN,
            "1.00",
            factor_codes=("EVENT_DIRECTION",),
        )


def test_valid_batch_returns_stable_input_hash() -> None:
    candidate = batch(signal("600000.SH", SignalAction.OPEN, "0.20"), cash="0.80")

    first = validate_signal_batch(candidate, portfolio(), {"600000.SH"})
    second = validate_signal_batch(candidate, portfolio(), {"600000.SH"})

    assert first.input_hash == second.input_hash
    assert len(first.input_hash) == 64
