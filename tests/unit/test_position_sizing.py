from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, Decimal, getcontext

from fkqt_jevinvestor.domain.decision import (
    DecisionAction,
    DecisionEvaluationStatus,
    DecisionEvaluationV1,
)
from fkqt_jevinvestor.domain.market_features import FeatureValue, MarketFeatureSnapshot
from fkqt_jevinvestor.domain.portfolio import PortfolioState, PositionState
from fkqt_jevinvestor.domain.signals import SignalAction
from fkqt_jevinvestor.services.position_sizing import (
    PositionSizingConfigV1,
    SizingStatus,
    build_position_sizing_run,
    size_one,
    to_validated_signal_batch,
)


def _evaluation(symbol: str, action: DecisionAction) -> DecisionEvaluationV1:
    available = action is not DecisionAction.NO_SIGNAL
    now = datetime(2026, 9, 18, 15, tzinfo=UTC)
    return DecisionEvaluationV1(
        evaluation_id=f"decision-{symbol}",
        formal_key="a" * 64,
        input_hash="b" * 64,
        symbol=symbol,
        status=(
            DecisionEvaluationStatus.AVAILABLE
            if available
            else DecisionEvaluationStatus.DATA_UNAVAILABLE
        ),
        action=action,
        thesis="冻结证据支持该动作。" if available else None,
        invalidation="冻结证据失效。" if available else None,
        raw_response_text='{"action":"ENTER"}' if available else None,
        raw_response_hash="c" * 64 if available else None,
        provider_name="deepseek",
        provider_version="responses-v1",
        model_id="deepseek-flash",
        prompt_version="decision-prompt-v1",
        output_schema_version="decision-output-v1",
        started_at=now,
        finished_at=now,
        latency_ms=1,
        error_code=None if available else "FEATURE_REQUIRED",
    )


def _features(
    symbol: str,
    *,
    volatility: str | None = "0.02000000",
    liquidity: str | None = "0.50000000",
) -> MarketFeatureSnapshot:
    cutoff = datetime(2026, 9, 18, 15, tzinfo=UTC)

    def value(code: str, raw: str | None) -> FeatureValue:
        return FeatureValue(
            feature_code=code,
            feature_version="market-features-v1",
            as_of=cutoff,
            lookback_window=20,
            value=Decimal(raw) if raw is not None else None,
            missing_reason=None if raw is not None else "SOURCE_UNAVAILABLE",
            source_snapshot_hash="d" * 64,
        )

    return MarketFeatureSnapshot(
        symbol=symbol,
        decision_date=date(2026, 9, 18),
        values={
            "realized_vol_20d": value("realized_vol_20d", volatility),
            "liquidity_percentile": value("liquidity_percentile", liquidity),
        },
        content_hash="e" * 64,
    )


def _position(symbol: str, weight: str) -> PositionState:
    return PositionState(
        symbol=symbol,
        quantity=1000,
        sellable_quantity=1000,
        average_cost=Decimal(10),
        total_cost=Decimal(10000),
        last_price=Decimal(10),
        current_position_pct=Decimal(weight),
        unrealized_pnl=Decimal(0),
        holding_trading_days=5,
    )


def _portfolio(*positions: PositionState) -> PortfolioState:
    return PortfolioState(
        portfolio_id="paper-main",
        cash_balance=Decimal(1000000),
        frozen_cash=Decimal(0),
        realized_pnl=Decimal(0),
        positions=positions,
        version=1,
    )


def _run(
    evaluations: tuple[DecisionEvaluationV1, ...],
    features: dict[str, MarketFeatureSnapshot],
    portfolio: PortfolioState | None = None,
):
    return build_position_sizing_run(
        run_id="run-1",
        decision_date=date(2026, 9, 18),
        planned_execution_date=date(2026, 9, 21),
        portfolio=portfolio or _portfolio(),
        total_equity=Decimal(1000000),
        evaluations=evaluations,
        feature_snapshots=features,
        config=PositionSizingConfigV1(),
    )


def test_size_one_uses_volatility_and_liquidity_with_fixed_decimal_context() -> None:
    original_rounding = getcontext().rounding
    getcontext().rounding = ROUND_DOWN
    try:
        result = size_one(
            realized_vol_20d=Decimal("0.02000000"),
            liquidity_percentile=Decimal("0.50000000"),
            config=PositionSizingConfigV1(),
        )
    finally:
        observed_rounding = getcontext().rounding
        getcontext().rounding = original_rounding

    assert result == Decimal("0.05000000")
    assert observed_rounding is ROUND_DOWN


def test_size_one_applies_volatility_floor_liquidity_clamp_and_single_cap() -> None:
    config = PositionSizingConfigV1()

    assert size_one(Decimal("0.001"), Decimal(2), config) == Decimal("0.10000000")
    assert size_one(Decimal("0.02"), Decimal("0.01"), config) == Decimal("0.02500000")


def test_actions_map_to_deterministic_targets_and_signal_actions() -> None:
    portfolio = _portfolio(
        _position("000002.SZ", "0.08000000"),
        _position("000003.SZ", "0.08000000"),
    )
    evaluations = (
        _evaluation("000001.SZ", DecisionAction.ENTER),
        _evaluation("000002.SZ", DecisionAction.KEEP),
        _evaluation("000003.SZ", DecisionAction.EXIT),
        _evaluation("000004.SZ", DecisionAction.AVOID),
    )
    features = {item.symbol: _features(item.symbol) for item in evaluations}

    run = _run(evaluations, features, portfolio)
    by_symbol = {target.symbol: target for target in run.targets}

    assert by_symbol["000001.SZ"].target_position_pct == Decimal("0.05000000")
    assert by_symbol["000001.SZ"].signal_action is SignalAction.OPEN
    assert by_symbol["000002.SZ"].target_position_pct == Decimal("0.05000000")
    assert by_symbol["000002.SZ"].signal_action is SignalAction.REDUCE
    assert by_symbol["000003.SZ"].target_position_pct == Decimal("0E-8")
    assert by_symbol["000003.SZ"].signal_action is SignalAction.CLOSE
    assert by_symbol["000004.SZ"].signal_action is SignalAction.AVOID
    assert run.gross_target_pct == Decimal("0.10000000")
    assert run.cash_target_pct == Decimal("0.90000000")


def test_missing_or_illiquid_entry_is_blocked_but_keep_never_becomes_exit() -> None:
    portfolio = _portfolio(_position("000002.SZ", "0.04000000"))
    evaluations = (
        _evaluation("000001.SZ", DecisionAction.ENTER),
        _evaluation("000002.SZ", DecisionAction.KEEP),
    )
    features = {
        "000001.SZ": _features("000001.SZ", liquidity="0.10000000"),
        "000002.SZ": _features("000002.SZ", volatility=None),
    }

    run = _run(evaluations, features, portfolio)
    entry, keep = run.targets

    assert entry.status is SizingStatus.BLOCKED
    assert entry.block_code == "LIQUIDITY_ENTRY_FLOOR"
    assert entry.target_position_pct == 0
    assert entry.signal_action is SignalAction.AVOID
    assert keep.status is SizingStatus.UNCHANGED
    assert keep.target_position_pct == Decimal("0.04000000")
    assert keep.signal_action is SignalAction.HOLD


def test_no_signal_preserves_preexisting_weight_and_blocks_new_risk_over_limit() -> None:
    portfolio = _portfolio(_position("000001.SZ", "0.85000000"))
    evaluations = (
        _evaluation("000001.SZ", DecisionAction.NO_SIGNAL),
        _evaluation("000002.SZ", DecisionAction.ENTER),
    )
    features = {item.symbol: _features(item.symbol) for item in evaluations}

    run = _run(evaluations, features, portfolio)
    held, entry = run.targets

    assert held.target_position_pct == Decimal("0.85000000")
    assert held.signal_action is SignalAction.HOLD
    assert entry.status is SizingStatus.BLOCKED
    assert entry.block_code == "PREEXISTING_GROSS_LIMIT_EXCEEDED"
    assert entry.target_position_pct == 0
    assert run.run_code == "PREEXISTING_GROSS_LIMIT_EXCEEDED"
    assert run.gross_target_pct == Decimal("0.85000000")


def test_input_order_does_not_change_targets_or_hashes() -> None:
    evaluations = (
        _evaluation("000002.SZ", DecisionAction.ENTER),
        _evaluation("000001.SZ", DecisionAction.ENTER),
    )
    features = {item.symbol: _features(item.symbol) for item in evaluations}

    left = _run(evaluations, features)
    right = _run(tuple(reversed(evaluations)), dict(reversed(tuple(features.items()))))

    assert left.targets == right.targets
    assert left.input_hash == right.input_hash
    assert left.target_batch_hash == right.target_batch_hash


def test_scaling_rounds_down_without_exceeding_gross_or_cash_limits() -> None:
    evaluations = tuple(
        _evaluation(f"{index:06d}.SZ", DecisionAction.ENTER)
        for index in range(1, 22)
    )
    features = {item.symbol: _features(item.symbol) for item in evaluations}

    run = _run(evaluations, features)

    assert run.gross_target_pct <= Decimal("0.80000000")
    assert run.cash_target_pct >= Decimal("0.20000000")


def test_scaled_entry_below_minimum_is_blocked_instead_of_opened() -> None:
    portfolio = _portfolio(_position("000001.SZ", "0.79500000"))
    evaluations = (
        _evaluation("000001.SZ", DecisionAction.NO_SIGNAL),
        _evaluation("000002.SZ", DecisionAction.ENTER),
    )
    features = {item.symbol: _features(item.symbol) for item in evaluations}

    run = _run(evaluations, features, portfolio)
    entry = next(item for item in run.targets if item.symbol == "000002.SZ")

    assert entry.status is SizingStatus.BLOCKED
    assert entry.block_code == "MIN_ENTRY_POSITION_AFTER_SCALING"
    assert entry.target_position_pct == Decimal(0)
    assert entry.signal_action is SignalAction.AVOID
    assert run.gross_target_pct == Decimal("0.79500000")


def test_formal_signal_adapter_uses_zero_confidence_sentinel() -> None:
    evaluations = (_evaluation("000001.SZ", DecisionAction.ENTER),)
    run = _run(evaluations, {"000001.SZ": _features("000001.SZ")})

    validated = to_validated_signal_batch(run)

    assert validated.batch.fixture_version == "decision-position-sizing-v1"
    assert validated.batch.signals[0].confidence == Decimal(0)
    assert validated.batch.signals[0].factor_codes == ()
    assert validated.batch.signals[0].evidence_ids == ()
    assert validated.input_hash == validated.batch.content_hash()
