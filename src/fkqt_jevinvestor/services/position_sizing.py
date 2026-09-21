from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from enum import StrEnum
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.decision import (
    DecisionAction,
    DecisionEvaluationV1,
)
from fkqt_jevinvestor.domain.market_features import MarketFeatureSnapshot
from fkqt_jevinvestor.domain.portfolio import PortfolioState
from fkqt_jevinvestor.domain.signals import (
    FixtureSignal,
    FixtureSignalBatch,
    SignalAction,
    ValidatedSignalBatch,
)
from fkqt_jevinvestor.ingestion.canonical import sha256_json

POSITION_SIZING_VERSION = "position-sizing-v1"
_WEIGHT_QUANTUM = Decimal("0.00000001")
_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_UP)


class SizingStatus(StrEnum):
    SIZED = "SIZED"
    UNCHANGED = "UNCHANGED"
    BLOCKED = "BLOCKED"


class PositionSizingConfigV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    daily_risk_budget: Decimal = Decimal("0.00200000")
    volatility_floor: Decimal = Decimal("0.01000000")
    max_single_position_pct: Decimal = Decimal("0.10000000")
    max_gross_position_pct: Decimal = Decimal("0.80000000")
    min_cash_pct: Decimal = Decimal("0.20000000")
    min_entry_position_pct: Decimal = Decimal("0.01000000")
    liquidity_entry_floor: Decimal = Decimal("0.20000000")
    weight_quantum: Decimal = _WEIGHT_QUANTUM

    @property
    def config_hash(self) -> str:
        return sha256_json(self)


class SizedTargetV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    decision_evaluation_id: str = Field(min_length=1)
    requested_action: DecisionAction
    status: SizingStatus
    current_position_pct: Decimal = Field(ge=0, le=1)
    raw_target_position_pct: Decimal = Field(ge=0, le=1)
    target_position_pct: Decimal = Field(ge=0, le=1)
    signal_action: SignalAction
    block_code: str | None
    thesis: str | None
    invalidation: str | None


class PositionSizingRunV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1)
    portfolio_id: str = Field(min_length=1)
    portfolio_version: int = Field(ge=1)
    decision_date: date
    planned_execution_date: date
    sizing_version: str = POSITION_SIZING_VERSION
    config_hash: str = Field(min_length=64, max_length=64)
    input_hash: str = Field(min_length=64, max_length=64)
    targets: tuple[SizedTargetV1, ...]
    gross_target_pct: Decimal = Field(ge=0, le=1)
    cash_target_pct: Decimal = Field(ge=0, le=1)
    target_batch_hash: str = Field(min_length=64, max_length=64)
    run_code: str | None


class DecisionSignalV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    requested_action: DecisionAction
    sizing_status: SizingStatus
    target_position_pct: Decimal = Field(ge=0, le=1)
    signal_action: SignalAction
    block_code: str | None
    decision_evaluation_id: str
    thesis: str | None
    invalidation: str | None


class DecisionSignalBatchV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    portfolio_id: str
    decision_date: date
    planned_execution_date: date
    sizing_version: str
    candidate_symbols: tuple[str, ...]
    signals: tuple[DecisionSignalV1, ...]
    cash_target_pct: Decimal = Field(ge=0, le=1)
    sizing_input_hash: str = Field(min_length=64, max_length=64)
    target_batch_hash: str = Field(min_length=64, max_length=64)


class _SizingDraft(TypedDict):
    evaluation: DecisionEvaluationV1
    current: Decimal
    raw: Decimal
    target: Decimal
    status: SizingStatus
    block_code: str | None


def _quantize(value: Decimal, quantum: Decimal = _WEIGHT_QUANTUM) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def size_one(
    realized_vol_20d: Decimal,
    liquidity_percentile: Decimal,
    config: PositionSizingConfigV1,
) -> Decimal:
    with localcontext(_DECIMAL_CONTEXT):
        volatility = max(realized_vol_20d, config.volatility_floor)
        liquidity = min(max(liquidity_percentile, Decimal("0.25")), Decimal(1))
        target = min(
            config.daily_risk_budget / volatility * liquidity,
            config.max_single_position_pct,
        )
        return _quantize(target, config.weight_quantum)


def _feature_decimal(
    snapshot: MarketFeatureSnapshot | None,
    code: str,
) -> Decimal | None:
    if snapshot is None:
        return None
    feature = snapshot.values.get(code)
    return feature.value if feature is not None else None


def _signal_action(
    action: DecisionAction,
    current: Decimal,
    target: Decimal,
) -> SignalAction:
    if action is DecisionAction.EXIT:
        return SignalAction.CLOSE
    if action is DecisionAction.AVOID:
        return SignalAction.AVOID
    if action is DecisionAction.NO_SIGNAL:
        return SignalAction.HOLD if current > 0 else SignalAction.AVOID
    if action is DecisionAction.ENTER:
        return SignalAction.OPEN if target > 0 else SignalAction.AVOID
    if target > current:
        return SignalAction.ADD
    if target < current:
        return SignalAction.REDUCE if target > 0 else SignalAction.HOLD
    return SignalAction.HOLD


def build_position_sizing_run(
    *,
    run_id: str,
    decision_date: date,
    planned_execution_date: date,
    portfolio: PortfolioState,
    total_equity: Decimal,
    evaluations: Sequence[DecisionEvaluationV1],
    feature_snapshots: Mapping[str, MarketFeatureSnapshot],
    config: PositionSizingConfigV1,
) -> PositionSizingRunV1:
    del total_equity  # Phase 4 只生成权重；D+1 数量由执行引擎计算。
    ordered = tuple(sorted(evaluations, key=lambda item: item.symbol))
    if len({item.symbol for item in ordered}) != len(ordered):
        raise ValueError("DUPLICATE_DECISION_SYMBOL")
    positions = {item.symbol: item for item in portfolio.positions}
    current_gross = sum(
        (item.current_position_pct for item in portfolio.positions), Decimal(0)
    )
    preexisting_over_limit = current_gross > config.max_gross_position_pct
    drafts: list[_SizingDraft] = []

    with localcontext(_DECIMAL_CONTEXT):
        for evaluation in ordered:
            symbol = evaluation.symbol
            current = positions.get(symbol)
            current_weight = (
                current.current_position_pct if current is not None else Decimal(0)
            )
            action = evaluation.action
            raw = Decimal(0)
            target = Decimal(0)
            status = SizingStatus.SIZED
            block_code: str | None = None
            snapshot = feature_snapshots.get(symbol)

            if action is DecisionAction.NO_SIGNAL:
                raw = current_weight
                target = current_weight
                status = SizingStatus.UNCHANGED
            elif action is DecisionAction.EXIT:
                target = Decimal(0)
            elif action is DecisionAction.AVOID:
                status = SizingStatus.UNCHANGED
            else:
                volatility = _feature_decimal(snapshot, "realized_vol_20d")
                liquidity = _feature_decimal(snapshot, "liquidity_percentile")
                if volatility is None or liquidity is None:
                    if action is DecisionAction.KEEP:
                        raw = current_weight
                        target = current_weight
                        status = SizingStatus.UNCHANGED
                        block_code = "SIZING_FEATURE_REQUIRED"
                    else:
                        status = SizingStatus.BLOCKED
                        block_code = "SIZING_FEATURE_REQUIRED"
                else:
                    raw = size_one(volatility, liquidity, config)
                    if (
                        action is DecisionAction.ENTER
                        and liquidity < config.liquidity_entry_floor
                    ):
                        status = SizingStatus.BLOCKED
                        block_code = "LIQUIDITY_ENTRY_FLOOR"
                    elif (
                        action is DecisionAction.ENTER
                        and raw < config.min_entry_position_pct
                    ):
                        status = SizingStatus.BLOCKED
                        block_code = "MIN_ENTRY_POSITION"
                    elif preexisting_over_limit and action is DecisionAction.ENTER:
                        status = SizingStatus.BLOCKED
                        block_code = "PREEXISTING_GROSS_LIMIT_EXCEEDED"
                    elif preexisting_over_limit and action is DecisionAction.KEEP:
                        target = min(raw, current_weight)
                    else:
                        target = raw
            drafts.append(
                {
                    "evaluation": evaluation,
                    "current": current_weight,
                    "raw": _quantize(raw, config.weight_quantum),
                    "target": _quantize(target, config.weight_quantum),
                    "status": status,
                    "block_code": block_code,
                }
            )

        if not preexisting_over_limit:
            protected = sum(
                (
                    item["target"]
                    for item in drafts
                    if item["status"] is not SizingStatus.SIZED
                ),
                Decimal(0),
            )
            scalable = [item for item in drafts if item["status"] is SizingStatus.SIZED]
            desired = sum((item["target"] for item in scalable), Decimal(0))
            available = max(config.max_gross_position_pct - protected, Decimal(0))
            if desired > available and desired > 0:
                scale = available / desired
                for item in scalable:
                    item["target"] = _quantize(
                        item["target"] * scale,
                        config.weight_quantum,
                    )

    targets: list[SizedTargetV1] = []
    for item in drafts:
        evaluation = item["evaluation"]
        current = item["current"]
        raw = item["raw"]
        target = item["target"]
        status = item["status"]
        if (
            evaluation.action is DecisionAction.KEEP
            and status is SizingStatus.SIZED
            and target == current
        ):
            status = SizingStatus.UNCHANGED
        targets.append(
            SizedTargetV1(
                symbol=evaluation.symbol,
                decision_evaluation_id=evaluation.evaluation_id,
                requested_action=evaluation.action,
                status=status,
                current_position_pct=current,
                raw_target_position_pct=raw,
                target_position_pct=target,
                signal_action=_signal_action(evaluation.action, current, target),
                block_code=item["block_code"],
                thesis=evaluation.thesis,
                invalidation=evaluation.invalidation,
            )
        )
    target_tuple = tuple(targets)
    gross = _quantize(
        sum((item.target_position_pct for item in target_tuple), Decimal(0)),
        config.weight_quantum,
    )
    cash = _quantize(Decimal(1) - gross, config.weight_quantum)
    input_hash = sha256_json(
        {
            "portfolio": portfolio.model_dump(mode="json"),
            "decision_date": decision_date.isoformat(),
            "planned_execution_date": planned_execution_date.isoformat(),
            "evaluations": [
                {
                    "evaluation_id": item.evaluation_id,
                    "input_hash": item.input_hash,
                    "symbol": item.symbol,
                    "action": item.action.value,
                }
                for item in ordered
            ],
            "feature_snapshot_hashes": {
                symbol: feature_snapshots[symbol].content_hash
                for symbol in sorted(feature_snapshots)
            },
            "config_hash": config.config_hash,
        }
    )
    target_batch_hash = sha256_json(
        {
            "sizing_version": POSITION_SIZING_VERSION,
            "input_hash": input_hash,
            "targets": [item.model_dump(mode="json") for item in target_tuple],
            "cash_target_pct": str(cash),
        }
    )
    return PositionSizingRunV1(
        run_id=run_id,
        portfolio_id=portfolio.portfolio_id,
        portfolio_version=portfolio.version,
        decision_date=decision_date,
        planned_execution_date=planned_execution_date,
        config_hash=config.config_hash,
        input_hash=input_hash,
        targets=target_tuple,
        gross_target_pct=gross,
        cash_target_pct=cash,
        target_batch_hash=target_batch_hash,
        run_code=(
            "PREEXISTING_GROSS_LIMIT_EXCEEDED" if preexisting_over_limit else None
        ),
    )


def build_decision_signal_batch(run: PositionSizingRunV1) -> DecisionSignalBatchV1:
    return DecisionSignalBatchV1(
        run_id=run.run_id,
        portfolio_id=run.portfolio_id,
        decision_date=run.decision_date,
        planned_execution_date=run.planned_execution_date,
        sizing_version=run.sizing_version,
        candidate_symbols=tuple(item.symbol for item in run.targets),
        signals=tuple(
            DecisionSignalV1(
                symbol=item.symbol,
                requested_action=item.requested_action,
                sizing_status=item.status,
                target_position_pct=item.target_position_pct,
                signal_action=item.signal_action,
                block_code=item.block_code,
                decision_evaluation_id=item.decision_evaluation_id,
                thesis=item.thesis,
                invalidation=item.invalidation,
            )
            for item in run.targets
        ),
        cash_target_pct=run.cash_target_pct,
        sizing_input_hash=run.input_hash,
        target_batch_hash=run.target_batch_hash,
    )


def to_validated_signal_batch(run: PositionSizingRunV1) -> ValidatedSignalBatch:
    formal = build_decision_signal_batch(run)
    fixture = FixtureSignalBatch(
        portfolio_id=formal.portfolio_id,
        decision_date=formal.decision_date,
        planned_execution_date=formal.planned_execution_date,
        fixture_version=f"decision-{formal.sizing_version}",
        candidate_symbols=formal.candidate_symbols,
        signals=tuple(
            FixtureSignal(
                symbol=item.symbol,
                action=item.signal_action,
                target_position_pct=item.target_position_pct,
                confidence=Decimal(0),
                thesis=item.thesis or "系统未获得有效模型动作。",
                invalidation=item.invalidation or "等待下一次正式决策。",
                factor_codes=(),
                evidence_ids=(),
            )
            for item in formal.signals
        ),
        cash_target_pct=formal.cash_target_pct,
    )
    return ValidatedSignalBatch(batch=fixture, input_hash=fixture.content_hash())
