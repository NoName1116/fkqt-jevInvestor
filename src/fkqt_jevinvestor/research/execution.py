from collections.abc import Mapping
from copy import deepcopy
from datetime import date
from decimal import Decimal

from fkqt_jevinvestor.domain.backtest import (
    BacktestExecutionResult,
    DecisionAction,
    TargetPositionBatch,
)
from fkqt_jevinvestor.domain.execution import ExecutionPolicy
from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot
from fkqt_jevinvestor.domain.portfolio import PortfolioState, PositionState
from fkqt_jevinvestor.domain.signals import (
    FixtureSignal,
    FixtureSignalBatch,
    SignalAction,
    ValidatedSignalBatch,
)
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.research.dataset import FrozenReplayDataProvider
from fkqt_jevinvestor.research.recorded import reject_legacy_rights
from fkqt_jevinvestor.services.execution_engine import build_order_drafts, execute_order_batch
from fkqt_jevinvestor.services.portfolio_service import PortfolioLedger


class ResearchExecutionPort:
    def __init__(self, replay: FrozenReplayDataProvider, policy: ExecutionPolicy) -> None:
        if policy != replay.manifest.execution_policy:
            raise ValueError("RESEARCH_EXECUTION_POLICY_MISMATCH")
        self.replay = replay
        self.policy = policy
        self._ledger: PortfolioLedger | None = None
        self._expected: PortfolioState | None = None
        self._cache: dict[str, BacktestExecutionResult] = {}
        self._executed_dates: tuple[date, ...] = ()

    async def execute(
        self,
        portfolio: PortfolioState,
        targets: TargetPositionBatch,
        market: Mapping[str, MarketExecutionSnapshot],
    ) -> BacktestExecutionResult:
        reject_legacy_rights(portfolio)
        day = self.replay.research_day(targets.decision_date)
        if (
            targets.planned_execution_date != day.snapshot.next_trade_date
            or market != day.execution.snapshots
        ):
            raise ValueError("RESEARCH_EXECUTION_INPUT_MISMATCH")
        if len({t.symbol for t in targets.targets}) != len(targets.targets):
            raise ValueError("RESEARCH_DUPLICATE_TARGET")
        required = {p.symbol for p in portfolio.positions} | {t.symbol for t in targets.targets}
        day.execution.verify(targets.planned_execution_date, required)
        if not {p.symbol for p in portfolio.positions}.issubset(
            {t.symbol for t in targets.targets}
        ):
            raise ValueError("RESEARCH_TARGET_HELD_COVERAGE_INCOMPLETE")
        key = sha256_json(
            {
                "portfolio": portfolio.model_dump(mode="json"),
                "targets": targets.model_dump(mode="json"),
                "market": {s: m.model_dump(mode="json") for s, m in market.items()},
                "policy": self.policy.model_dump(mode="json"),
            }
        )
        if key in self._cache:
            return self._cache[key]
        if portfolio.frozen_cash:
            raise ValueError("RESEARCH_FROZEN_CASH_UNSUPPORTED")
        if self._ledger is None:
            if portfolio.positions or portfolio.realized_pnl:
                raise ValueError("RESEARCH_INITIAL_FIFO_STATE_REQUIRED")
            ledger = PortfolioLedger(
                portfolio.portfolio_id, portfolio.cash_balance, portfolio.cash_balance
            )
        else:
            if portfolio != self._expected:
                raise ValueError("RESEARCH_EXECUTION_PORTFOLIO_MISMATCH")
            if (
                self._ledger.latest_nav is None
                or self._ledger.latest_nav.valuation_date != targets.decision_date
            ):
                raise ValueError("RESEARCH_EXECUTION_SEQUENCE_INVALID")
            ledger = deepcopy(self._ledger)
        signals: list[FixtureSignal] = []
        for target in targets.targets:
            position = portfolio.position(target.symbol)
            current = position.current_position_pct if position is not None else Decimal(0)
            action = target.action
            if action is DecisionAction.NO_SIGNAL:
                if target.target_position_pct != current:
                    raise ValueError("RESEARCH_NO_SIGNAL_WEIGHT_CHANGED")
                signal = SignalAction.HOLD if position is not None else SignalAction.AVOID
            elif action is DecisionAction.EXIT:
                if position is None or target.target_position_pct != 0:
                    raise ValueError("RESEARCH_ACTION_POSITION_MISMATCH")
                signal = SignalAction.CLOSE
            elif action is DecisionAction.ENTER:
                if position is not None:
                    raise ValueError("RESEARCH_ACTION_POSITION_MISMATCH")
                signal = SignalAction.OPEN if target.target_position_pct > 0 else SignalAction.AVOID
            elif action is DecisionAction.AVOID:
                if position is not None or target.target_position_pct != 0:
                    raise ValueError("RESEARCH_ACTION_POSITION_MISMATCH")
                signal = SignalAction.AVOID
            else:
                if position is None:
                    raise ValueError("RESEARCH_ACTION_POSITION_MISMATCH")
                signal = (
                    SignalAction.ADD
                    if target.target_position_pct > current
                    else (
                        SignalAction.REDUCE
                        if 0 < target.target_position_pct < current
                        else SignalAction.HOLD
                    )
                )
            signals.append(
                FixtureSignal(
                    symbol=target.symbol,
                    action=signal,
                    target_position_pct=target.target_position_pct,
                    confidence=Decimal(0),
                    thesis="冻结研究目标。",
                    invalidation="仅对指定交易日有效。",
                    factor_codes=(),
                    evidence_ids=(),
                )
            )
        gross = sum((t.target_position_pct for t in targets.targets), Decimal(0))
        if gross > 1:
            raise ValueError("RESEARCH_GROSS_TARGET_INVALID")
        fixture = FixtureSignalBatch(
            portfolio_id=portfolio.portfolio_id,
            decision_date=targets.decision_date,
            planned_execution_date=targets.planned_execution_date,
            fixture_version="research-execution-v1",
            candidate_symbols=tuple(t.symbol for t in targets.targets),
            signals=tuple(signals),
            cash_target_pct=1 - gross,
        )
        orders = build_order_drafts(
            ValidatedSignalBatch(batch=fixture, input_hash=fixture.content_hash()),
            portfolio,
            self.policy,
            targets.planned_execution_date,
        )
        execution = execute_order_batch(ledger, orders, market, targets.planned_execution_date)
        executed_dates = self._executed_dates + (targets.planned_execution_date,)
        equity = execution.nav.total_equity
        positions = tuple(
            PositionState(
                symbol=p.symbol,
                quantity=p.quantity,
                sellable_quantity=ledger.sellable_quantity(
                    p.symbol, targets.planned_execution_date
                ),
                average_cost=p.average_cost,
                total_cost=p.total_cost,
                last_price=p.last_price,
                current_position_pct=(p.market_value / equity).quantize(Decimal("0.00000001")),
                unrealized_pnl=p.unrealized_pnl,
                holding_trading_days=sum(
                    1
                    for executed_date in executed_dates
                    if p.lots
                    and p.lots[0].acquired_on <= executed_date <= targets.planned_execution_date
                ),
            )
            for p in sorted(ledger.positions, key=lambda p: p.symbol)
            if p.quantity > 0
        )
        after = PortfolioState(
            portfolio_id=portfolio.portfolio_id,
            cash_balance=ledger.cash_balance,
            frozen_cash=Decimal(0),
            realized_pnl=ledger.realized_pnl,
            positions=positions,
            version=portfolio.version + 1,
        )
        result = BacktestExecutionResult(
            decision_date=targets.decision_date,
            execution_date=targets.planned_execution_date,
            portfolio_after=after,
            total_equity=equity,
            cash_balance=execution.nav.cash_balance,
            market_value=execution.nav.market_value,
            gross_traded_value=sum((f.gross_value for f in execution.fills), Decimal(0)),
            total_fees=sum((f.total_fees for f in execution.fills), Decimal(0)),
            submitted_orders=len(execution.orders),
            filled_orders=sum(o.filled_quantity > 0 for o in execution.orders),
            rejected_orders=sum(
                o.filled_quantity == 0 and o.code is not None for o in execution.orders
            ),
        )
        self._ledger, self._expected = ledger, after
        self._executed_dates = executed_dates
        self._cache[key] = result
        return result
