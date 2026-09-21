from decimal import Decimal

from fkqt_jevinvestor.domain.portfolio import PortfolioState
from fkqt_jevinvestor.domain.signals import (
    FixtureSignalBatch,
    SignalAction,
    ValidatedSignalBatch,
)

WEIGHT_TOLERANCE = Decimal("0.0001")


class SignalValidationError(ValueError):
    def __init__(self, codes: tuple[str, ...]) -> None:
        self.codes = codes
        super().__init__(", ".join(codes))


def validate_signal_batch(
    batch: FixtureSignalBatch,
    portfolio: PortfolioState,
    allowed_symbols: set[str],
) -> ValidatedSignalBatch:
    errors: set[str] = set()
    symbols = [item.symbol for item in batch.signals]
    if len(symbols) != len(set(symbols)):
        errors.add("DUPLICATE_SYMBOL")

    current_symbols = {item.symbol for item in portfolio.positions if item.quantity > 0}
    required_symbols = allowed_symbols | current_symbols
    if set(symbols) != required_symbols:
        errors.add("SIGNAL_COVERAGE_INCOMPLETE")
    if any(symbol not in required_symbols for symbol in symbols):
        errors.add("SYMBOL_NOT_ALLOWED")

    total_weight = batch.cash_target_pct + sum(
        (item.target_position_pct for item in batch.signals),
        start=Decimal(0),
    )
    if abs(total_weight - Decimal(1)) > WEIGHT_TOLERANCE:
        errors.add("TARGET_WEIGHT_SUM_INVALID")

    for item in batch.signals:
        position = portfolio.position(item.symbol)
        current_quantity = position.quantity if position is not None else 0
        current_pct = position.current_position_pct if position is not None else Decimal(0)
        if item.action is SignalAction.OPEN and current_quantity > 0:
            errors.add("OPEN_REQUIRES_EMPTY_POSITION")
        elif item.action is SignalAction.ADD and (
            current_quantity == 0 or item.target_position_pct <= current_pct
        ):
            errors.add("ADD_MUST_INCREASE_POSITION")
        elif item.action is SignalAction.REDUCE and (
            current_quantity == 0 or item.target_position_pct >= current_pct
        ):
            errors.add("REDUCE_MUST_DECREASE_POSITION")
        elif item.action is SignalAction.CLOSE and (
            current_quantity == 0 or item.target_position_pct != 0
        ):
            errors.add("CLOSE_REQUIRES_ZERO_TARGET")
        elif item.action is SignalAction.HOLD and item.target_position_pct != current_pct:
            errors.add("HOLD_MUST_KEEP_POSITION")
        elif item.action is SignalAction.AVOID and (
            current_quantity > 0 or item.target_position_pct != 0
        ):
            errors.add("AVOID_REQUIRES_ZERO_TARGET")

    if errors:
        raise SignalValidationError(tuple(sorted(errors)))
    return ValidatedSignalBatch(batch=batch, input_hash=batch.content_hash())
