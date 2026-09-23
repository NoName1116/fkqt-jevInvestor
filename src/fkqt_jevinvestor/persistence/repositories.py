import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.domain.execution import ExecutionPolicy, OrderSide, VirtualOrderStatus
from fkqt_jevinvestor.domain.portfolio import PortfolioState, PositionState
from fkqt_jevinvestor.domain.signals import FixtureSignalBatch, SignalAction, ValidatedSignalBatch
from fkqt_jevinvestor.persistence.models import (
    NavRecord,
    PortfolioRecord,
    PortfolioSnapshotRecord,
    PositionLotRecord,
    PositionRecord,
    PositionSizingRunRecord,
    PositionTargetRecord,
    SignalBatchRecord,
    SignalRecord,
    VirtualFillRecord,
    VirtualOrderRecord,
)
from fkqt_jevinvestor.services.execution_engine import (
    ExecutionBatchResult,
    VirtualFill,
    VirtualOrderDraft,
    build_order_drafts,
    execute_order_batch,
)
from fkqt_jevinvestor.services.portfolio_service import (
    CreatePortfolio,
    ExecuteTradeDate,
    NavState,
    PortfolioLedger,
    PortfolioSnapshot,
    PositionLot,
)
from fkqt_jevinvestor.services.position_sizing import (
    PositionSizingConfigV1,
    PositionSizingRunV1,
)

SessionFactory = async_sessionmaker[AsyncSession]


class PortfolioRepositoryError(RuntimeError):
    pass


class PortfolioAlreadyExists(PortfolioRepositoryError):
    pass


class PortfolioNotFound(PortfolioRepositoryError):
    pass


class PortfolioVersionConflict(PortfolioRepositoryError):
    pass


class PortfolioTransactionError(PortfolioRepositoryError):
    pass


class StoredOrder(VirtualOrderDraft):
    signal_id: str


class StoredSignalBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch_id: str
    input_hash: str
    orders: tuple[StoredOrder, ...]


class PortfolioRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def create(self, command: CreatePortfolio) -> PortfolioState:
        now = datetime.now(UTC)
        cash = command.initial_cash.quantize(Decimal("0.0001"))
        ledger = PortfolioLedger(command.portfolio_id, cash, cash)
        snapshot = ledger.snapshot("INITIAL", now)
        try:
            async with self._session_factory.begin() as session:
                session.add(
                    PortfolioRecord(
                        id=command.portfolio_id,
                        name=command.name,
                        base_currency="CNY",
                        initial_cash=cash,
                        cash_balance=cash,
                        frozen_cash=Decimal(0),
                        realized_pnl=Decimal(0),
                        status="ACTIVE",
                        version=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add(_snapshot_record(snapshot))
        except IntegrityError as exc:
            raise PortfolioAlreadyExists(command.portfolio_id) from exc
        return PortfolioState(
            portfolio_id=command.portfolio_id,
            cash_balance=cash,
            frozen_cash=Decimal(0),
            realized_pnl=Decimal(0),
            version=1,
        )

    async def get_state(self, portfolio_id: str, as_of: date | None = None) -> PortfolioState:
        async with self._session_factory() as session:
            portfolio = await session.get(PortfolioRecord, portfolio_id)
            if portfolio is None:
                raise PortfolioNotFound(portfolio_id)
            positions = tuple(
                (await session.scalars(
                    select(PositionRecord)
                    .where(PositionRecord.portfolio_id == portfolio_id)
                    .order_by(PositionRecord.symbol)
                )).all()
            )
            lots = tuple(
                (await session.scalars(
                    select(PositionLotRecord).where(
                        PositionLotRecord.portfolio_id == portfolio_id
                    )
                )).all()
            )
            nav_dates = tuple((await session.scalars(
                select(NavRecord.valuation_date)
                .where(NavRecord.portfolio_id == portfolio_id)
                .order_by(NavRecord.valuation_date)
            )).all())

        if as_of is not None and nav_dates and nav_dates[-1] > as_of:
            raise PortfolioTransactionError("PORTFOLIO_HISTORICAL_STATE_UNAVAILABLE")

        total_equity = portfolio.cash_balance + sum(
            (item.last_price * item.quantity for item in positions),
            Decimal(0),
        )
        state_date = as_of or (nav_dates[-1] if nav_dates else date.min)
        lot_quantities: dict[str, int] = {}
        first_acquired: dict[str, date] = {}
        for lot in lots:
            first_acquired[lot.symbol] = min(first_acquired.get(lot.symbol, lot.acquired_on), lot.acquired_on)
            if lot.acquired_on < state_date:
                lot_quantities[lot.symbol] = lot_quantities.get(lot.symbol, 0) + lot.remaining_quantity
        position_states = tuple(
            PositionState(
                symbol=item.symbol,
                quantity=item.quantity,
                sellable_quantity=lot_quantities.get(item.symbol, 0),
                average_cost=item.average_cost,
                total_cost=item.total_cost,
                last_price=item.last_price,
                current_position_pct=(
                    item.last_price * item.quantity / total_equity
                    if total_equity > 0
                    else Decimal(0)
                ),
                unrealized_pnl=item.last_price * item.quantity - item.total_cost,
                holding_trading_days=sum(
                    acquired <= nav_date <= state_date
                    for nav_date in nav_dates
                    for acquired in (first_acquired.get(item.symbol, state_date),)
                ),
            )
            for item in positions
        )
        return PortfolioState(
            portfolio_id=portfolio.id,
            cash_balance=portfolio.cash_balance,
            frozen_cash=portfolio.frozen_cash,
            realized_pnl=portfolio.realized_pnl,
            positions=position_states,
            version=portfolio.version,
        )

    async def save_signal_batch(self, batch: ValidatedSignalBatch) -> StoredSignalBatch:
        async with self._session_factory.begin() as session:
            existing = await session.scalar(
                select(SignalBatchRecord).where(
                    SignalBatchRecord.portfolio_id == batch.batch.portfolio_id,
                    SignalBatchRecord.decision_date == batch.batch.decision_date,
                    SignalBatchRecord.fixture_version == batch.batch.fixture_version,
                )
            )
            if existing is not None:
                return await self._stored_batch(session, existing)

            state = await self.get_state(
                batch.batch.portfolio_id, batch.batch.decision_date
            )
            policy = ExecutionPolicy()
            next_trade_date = batch.batch.planned_execution_date
            drafts = build_order_drafts(batch, state, policy, next_trade_date)
            now = datetime.now(UTC)
            batch_id = _stable_id("batch", batch.input_hash)
            decision_snapshot_id = _stable_id("snapshot", f"decision:{batch.input_hash}")
            decision_details = state.model_dump(mode="json")
            decision_payload = json.dumps(
                decision_details, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            session.add(
                PortfolioSnapshotRecord(
                    id=decision_snapshot_id,
                    portfolio_id=batch.batch.portfolio_id,
                    snapshot_type="DECISION_INPUT",
                    as_of=datetime.combine(batch.batch.decision_date, time.min, tzinfo=UTC),
                    cash_balance=state.cash_balance,
                    frozen_cash=state.frozen_cash,
                    market_value=sum(
                        (item.last_price * item.quantity for item in state.positions), Decimal(0)
                    ),
                    total_equity=state.cash_balance + sum(
                        (item.last_price * item.quantity for item in state.positions), Decimal(0)
                    ),
                    realized_pnl=state.realized_pnl,
                    unrealized_pnl=sum((item.unrealized_pnl for item in state.positions), Decimal(0)),
                    content_hash=hashlib.sha256(decision_payload.encode("utf-8")).hexdigest(),
                    details_json=decision_details,
                )
            )
            session.add(
                SignalBatchRecord(
                    id=batch_id,
                    portfolio_id=batch.batch.portfolio_id,
                    decision_date=batch.batch.decision_date,
                    fixture_version=batch.batch.fixture_version,
                    cash_target_pct=batch.batch.cash_target_pct,
                    status=batch.status.value,
                    input_hash=batch.input_hash,
                    decision_snapshot_id=decision_snapshot_id,
                    created_at=now,
                )
            )
            signal_ids: dict[str, str] = {}
            for signal in batch.batch.signals:
                signal_id = _stable_id("signal", f"{batch_id}:{signal.symbol}")
                signal_ids[signal.symbol] = signal_id
                session.add(
                    SignalRecord(
                        id=signal_id,
                        batch_id=batch_id,
                        symbol=signal.symbol,
                        action=signal.action.value,
                        target_position_pct=signal.target_position_pct,
                        confidence=signal.confidence,
                        thesis=signal.thesis,
                        invalidation=signal.invalidation,
                        created_at=now,
                    )
                )
            stored_orders = tuple(
                StoredOrder(signal_id=signal_ids[draft.symbol], **draft.model_dump())
                for draft in drafts
            )
            for order in stored_orders:
                session.add(_order_record(order, batch.batch.portfolio_id, now))
            return StoredSignalBatch(
                batch_id=batch_id,
                input_hash=batch.input_hash,
                orders=stored_orders,
            )

    async def save_c_group_signal_batch(
        self,
        sizing_run: PositionSizingRunV1,
        sizing_config: PositionSizingConfigV1,
        batch: ValidatedSignalBatch,
        decision_portfolio: PortfolioState,
    ) -> StoredSignalBatch:
        if (
            sizing_run.config_hash != sizing_config.config_hash
            or sizing_run.portfolio_id != batch.batch.portfolio_id
            or sizing_run.portfolio_id != decision_portfolio.portfolio_id
            or sizing_run.portfolio_version != decision_portfolio.version
            or sizing_run.decision_date != batch.batch.decision_date
            or sizing_run.planned_execution_date
            != batch.batch.planned_execution_date
            or tuple(item.symbol for item in sizing_run.targets)
            != batch.batch.candidate_symbols
        ):
            raise PortfolioTransactionError("C_GROUP_PERSISTENCE_IDENTITY_MISMATCH")
        try:
            async with self._session_factory.begin() as session:
                existing_run = await session.scalar(
                    select(PositionSizingRunRecord).where(
                        PositionSizingRunRecord.run_id == sizing_run.run_id
                    )
                )
                if existing_run is not None:
                    if (
                        existing_run.input_hash != sizing_run.input_hash
                        or existing_run.target_batch_hash
                        != sizing_run.target_batch_hash
                        or existing_run.signal_batch_id is None
                    ):
                        raise PortfolioTransactionError(
                            "C_GROUP_IDEMPOTENCY_CONFLICT"
                        )
                    signal_record = await session.get(
                        SignalBatchRecord, existing_run.signal_batch_id
                    )
                    if signal_record is None:
                        raise PortfolioTransactionError(
                            "C_GROUP_SIGNAL_BATCH_NOT_FOUND"
                        )
                    return await self._stored_batch(session, signal_record)

                portfolio_record = await session.get(
                    PortfolioRecord, sizing_run.portfolio_id
                )
                if portfolio_record is None:
                    raise PortfolioNotFound(sizing_run.portfolio_id)
                if portfolio_record.version != sizing_run.portfolio_version:
                    raise PortfolioVersionConflict("PORTFOLIO_VERSION_CONFLICT")

                policy = ExecutionPolicy()
                drafts = build_order_drafts(
                    batch,
                    decision_portfolio,
                    policy,
                    batch.batch.planned_execution_date,
                )
                now = datetime.now(UTC)
                batch_id = _stable_id("batch", batch.input_hash)
                decision_snapshot_id = _stable_id(
                    "snapshot", f"decision:{batch.input_hash}"
                )
                decision_details = decision_portfolio.model_dump(mode="json")
                decision_payload = json.dumps(
                    decision_details,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                decision_portfolio_hash = hashlib.sha256(
                    decision_payload.encode("utf-8")
                ).hexdigest()
                existing_batch = await session.get(SignalBatchRecord, batch_id)
                if existing_batch is not None:
                    if existing_batch.input_hash != batch.input_hash:
                        raise PortfolioTransactionError(
                            "C_GROUP_SIGNAL_CONTENT_CONFLICT"
                        )
                    self._add_c_group_sizing_records(
                        session=session,
                        sizing_run=sizing_run,
                        sizing_config=sizing_config,
                        decision_portfolio_hash=decision_portfolio_hash,
                        batch_id=batch_id,
                        now=now,
                    )
                    await session.flush()
                    return await self._stored_batch(session, existing_batch)
                market_value = sum(
                    (
                        item.last_price * item.quantity
                        for item in decision_portfolio.positions
                    ),
                    Decimal(0),
                )
                session.add(
                    PortfolioSnapshotRecord(
                        id=decision_snapshot_id,
                        portfolio_id=sizing_run.portfolio_id,
                        snapshot_type="DECISION_INPUT",
                        as_of=datetime.combine(
                            sizing_run.decision_date, time.min, tzinfo=UTC
                        ),
                        cash_balance=decision_portfolio.cash_balance,
                        frozen_cash=decision_portfolio.frozen_cash,
                        market_value=market_value,
                        total_equity=decision_portfolio.cash_balance + market_value,
                        realized_pnl=decision_portfolio.realized_pnl,
                        unrealized_pnl=sum(
                            (
                                item.unrealized_pnl
                                for item in decision_portfolio.positions
                            ),
                            Decimal(0),
                        ),
                        content_hash=decision_portfolio_hash,
                        details_json=decision_details,
                    )
                )
                session.add(
                    SignalBatchRecord(
                        id=batch_id,
                        portfolio_id=sizing_run.portfolio_id,
                        decision_date=sizing_run.decision_date,
                        fixture_version=batch.batch.fixture_version,
                        cash_target_pct=batch.batch.cash_target_pct,
                        status=batch.status.value,
                        input_hash=batch.input_hash,
                        decision_snapshot_id=decision_snapshot_id,
                        created_at=now,
                    )
                )
                signal_ids: dict[str, str] = {}
                for signal in batch.batch.signals:
                    signal_id = _stable_id("signal", f"{batch_id}:{signal.symbol}")
                    signal_ids[signal.symbol] = signal_id
                    session.add(
                        SignalRecord(
                            id=signal_id,
                            batch_id=batch_id,
                            symbol=signal.symbol,
                            action=signal.action.value,
                            target_position_pct=signal.target_position_pct,
                            confidence=signal.confidence,
                            thesis=signal.thesis,
                            invalidation=signal.invalidation,
                            created_at=now,
                        )
                    )
                stored_orders = tuple(
                    StoredOrder(signal_id=signal_ids[draft.symbol], **draft.model_dump())
                    for draft in drafts
                )
                for order in stored_orders:
                    session.add(_order_record(order, sizing_run.portfolio_id, now))

                self._add_c_group_sizing_records(
                    session=session,
                    sizing_run=sizing_run,
                    sizing_config=sizing_config,
                    decision_portfolio_hash=decision_portfolio_hash,
                    batch_id=batch_id,
                    now=now,
                )
                await session.flush()
                return StoredSignalBatch(
                    batch_id=batch_id,
                    input_hash=batch.input_hash,
                    orders=stored_orders,
                )
        except IntegrityError as exc:
            raise PortfolioTransactionError("C_GROUP_ATOMIC_WRITE_FAILED") from exc

    @staticmethod
    def _add_c_group_sizing_records(
        *,
        session: AsyncSession,
        sizing_run: PositionSizingRunV1,
        sizing_config: PositionSizingConfigV1,
        decision_portfolio_hash: str,
        batch_id: str,
        now: datetime,
    ) -> None:
        sizing_run_id = _stable_id("sizing", sizing_run.run_id)
        session.add(
            PositionSizingRunRecord(
                id=sizing_run_id,
                run_id=sizing_run.run_id,
                portfolio_id=sizing_run.portfolio_id,
                portfolio_version=sizing_run.portfolio_version,
                decision_date=sizing_run.decision_date,
                planned_execution_date=sizing_run.planned_execution_date,
                sizing_version=sizing_run.sizing_version,
                config_json=sizing_config.model_dump(mode="json"),
                config_hash=sizing_run.config_hash,
                input_hash=sizing_run.input_hash,
                decision_portfolio_hash=decision_portfolio_hash,
                target_batch_hash=sizing_run.target_batch_hash,
                gross_target_pct=sizing_run.gross_target_pct,
                cash_target_pct=sizing_run.cash_target_pct,
                run_code=sizing_run.run_code,
                signal_batch_id=batch_id,
                created_at=now,
            )
        )
        for target in sizing_run.targets:
            session.add(
                PositionTargetRecord(
                    id=_stable_id(
                        "sizing-target", f"{sizing_run_id}:{target.symbol}"
                    ),
                    sizing_run_id=sizing_run_id,
                    decision_evaluation_id=target.decision_evaluation_id,
                    symbol=target.symbol,
                    requested_action=target.requested_action.value,
                    sizing_status=target.status.value,
                    current_position_pct=target.current_position_pct,
                    raw_target_position_pct=target.raw_target_position_pct,
                    target_position_pct=target.target_position_pct,
                    signal_action=target.signal_action.value,
                    block_code=target.block_code,
                )
            )

    async def find_signal_batch(self, batch: FixtureSignalBatch) -> StoredSignalBatch | None:
        async with self._session_factory() as session:
            record = await session.scalar(
                select(SignalBatchRecord).where(
                    SignalBatchRecord.portfolio_id == batch.portfolio_id,
                    SignalBatchRecord.decision_date == batch.decision_date,
                    SignalBatchRecord.fixture_version == batch.fixture_version,
                )
            )
            if record is None:
                return None
            if record.input_hash != batch.content_hash():
                raise PortfolioTransactionError("FIXTURE_IDEMPOTENCY_CONFLICT")
            return await self._stored_batch(session, record)

    async def load_pending_orders(
        self,
        portfolio_id: str,
        trade_date: date,
    ) -> tuple[StoredOrder, ...]:
        async with self._session_factory() as session:
            records = tuple(
                (await session.scalars(
                    select(VirtualOrderRecord)
                    .where(
                        VirtualOrderRecord.portfolio_id == portfolio_id,
                        VirtualOrderRecord.planned_execution_date == trade_date,
                        VirtualOrderRecord.status == VirtualOrderStatus.PENDING_NEXT_OPEN.value,
                    )
                    .order_by(VirtualOrderRecord.id)
                )).all()
            )
        return tuple(_stored_order(item) for item in records)

    async def load_pending_orders_through(
        self,
        portfolio_id: str,
        trade_date: date,
    ) -> tuple[StoredOrder, ...]:
        async with self._session_factory() as session:
            records = tuple((await session.scalars(
                select(VirtualOrderRecord)
                .where(
                    VirtualOrderRecord.portfolio_id == portfolio_id,
                    VirtualOrderRecord.planned_execution_date <= trade_date,
                    VirtualOrderRecord.status == VirtualOrderStatus.PENDING_NEXT_OPEN.value,
                )
                .order_by(VirtualOrderRecord.id)
            )).all())
        return tuple(_stored_order(item) for item in records)

    async def execute_trade_date(self, command: ExecuteTradeDate) -> ExecutionBatchResult:
        try:
            async with self._session_factory.begin() as session:
                existing_portfolio = await session.get(PortfolioRecord, command.portfolio_id)
                if existing_portfolio is None:
                    raise PortfolioNotFound(command.portfolio_id)
                version_result = await session.execute(
                    update(PortfolioRecord)
                    .where(
                        PortfolioRecord.id == command.portfolio_id,
                        PortfolioRecord.version == command.expected_version,
                    )
                    .values(version=command.expected_version + 1, updated_at=datetime.now(UTC))
                )
                if getattr(version_result, "rowcount", 0) != 1:
                    raise PortfolioVersionConflict("PORTFOLIO_VERSION_CONFLICT")

                portfolio = await session.get(PortfolioRecord, command.portfolio_id)
                if portfolio is None:
                    raise PortfolioNotFound(command.portfolio_id)
                order_records = tuple(
                    (await session.scalars(
                        select(VirtualOrderRecord)
                        .where(
                            VirtualOrderRecord.portfolio_id == command.portfolio_id,
                            VirtualOrderRecord.planned_execution_date <= command.trade_date,
                            VirtualOrderRecord.status == VirtualOrderStatus.PENDING_NEXT_OPEN.value,
                        )
                        .order_by(VirtualOrderRecord.id)
                    )).all()
                )
                existing_nav = await session.scalar(select(NavRecord).where(
                    NavRecord.portfolio_id == command.portfolio_id,
                    NavRecord.valuation_date == command.trade_date,
                ))
                if existing_nav is not None:
                    portfolio.version = command.expected_version
                    return await self._existing_execution(session, command, order_records)

                ledger = await self._ledger(session, portfolio)
                result = execute_order_batch(
                    ledger,
                    tuple(_stored_order(item) for item in order_records),
                    command.market_snapshots,
                    command.trade_date,
                )
                portfolio.cash_balance = ledger.cash_balance
                portfolio.frozen_cash = ledger.frozen_cash
                portfolio.realized_pnl = ledger.realized_pnl
                await self._replace_positions(session, command.portfolio_id, ledger, result)
                order_by_id = {item.id: item for item in order_records}
                for order in result.orders:
                    record = order_by_id[order.order_id]
                    record.status = order.status.value
                    record.status_code = order.code
                    record.intended_quantity = order.intended_quantity
                    record.filled_quantity = order.filled_quantity
                    record.remaining_quantity = order.remaining_quantity
                    record.updated_at = datetime.now(UTC)
                for fill in result.fills:
                    session.add(_fill_record(fill))
                session.add(_snapshot_record(result.snapshot, command.execution_input_hash))
                session.add(_nav_record(command.portfolio_id, result.nav))
                await session.flush()
                return result
        except PortfolioRepositoryError:
            raise
        except (IntegrityError, OperationalError) as exc:
            raise PortfolioTransactionError("PORTFOLIO_TRANSACTION_FAILED") from exc

    async def list_nav(self, portfolio_id: str) -> tuple[NavState, ...]:
        async with self._session_factory() as session:
            records = tuple(
                (await session.scalars(
                    select(NavRecord)
                    .where(NavRecord.portfolio_id == portfolio_id)
                    .order_by(NavRecord.valuation_date)
                )).all()
            )
            snapshots = tuple(
                (await session.scalars(
                    select(PortfolioSnapshotRecord).where(
                        PortfolioSnapshotRecord.portfolio_id == portfolio_id,
                        PortfolioSnapshotRecord.snapshot_type == "POST_EXECUTION",
                    )
                )).all()
            )
        snapshot_by_date = {item.as_of.date(): item for item in snapshots}
        return tuple(_nav_state(item, snapshot_by_date.get(item.valuation_date)) for item in records)

    async def _stored_batch(
        self,
        session: AsyncSession,
        batch: SignalBatchRecord,
    ) -> StoredSignalBatch:
        records = tuple(
            (await session.scalars(
                select(VirtualOrderRecord)
                .join(SignalRecord, VirtualOrderRecord.signal_id == SignalRecord.id)
                .where(SignalRecord.batch_id == batch.id)
                .order_by(VirtualOrderRecord.id)
            )).all()
        )
        return StoredSignalBatch(
            batch_id=batch.id,
            input_hash=batch.input_hash,
            orders=tuple(_stored_order(item) for item in records),
        )

    async def _ledger(
        self,
        session: AsyncSession,
        portfolio: PortfolioRecord,
    ) -> PortfolioLedger:
        ledger = PortfolioLedger(
            portfolio.id,
            portfolio.initial_cash,
            portfolio.cash_balance,
            frozen_cash=portfolio.frozen_cash,
            realized_pnl=portfolio.realized_pnl,
        )
        positions = tuple(
            (await session.scalars(
                select(PositionRecord).where(PositionRecord.portfolio_id == portfolio.id)
            )).all()
        )
        lots = tuple(
            (await session.scalars(
                select(PositionLotRecord)
                .where(PositionLotRecord.portfolio_id == portfolio.id)
                .order_by(PositionLotRecord.acquired_on, PositionLotRecord.id)
            )).all()
        )
        position_by_id = {item.id: item for item in positions}
        for lot in lots:
            record = position_by_id[lot.position_id]
            position = ledger.position(record.symbol)
            position.last_price = record.last_price
            position.lots.append(
                PositionLot(
                    acquired_on=lot.acquired_on,
                    remaining_quantity=lot.remaining_quantity,
                    total_cost=lot.total_cost,
                )
            )
        nav_records = tuple((await session.scalars(
            select(NavRecord)
            .where(NavRecord.portfolio_id == portfolio.id)
            .order_by(NavRecord.valuation_date)
        )).all())
        snapshot_records = tuple((await session.scalars(
            select(PortfolioSnapshotRecord).where(
                PortfolioSnapshotRecord.portfolio_id == portfolio.id,
                PortfolioSnapshotRecord.snapshot_type == "POST_EXECUTION",
            )
        )).all())
        snapshots_by_date = {item.as_of.date(): item for item in snapshot_records}
        ledger.restore_nav_history(tuple(
            _nav_state(item, snapshots_by_date.get(item.valuation_date)) for item in nav_records
        ))
        return ledger

    async def _replace_positions(
        self,
        session: AsyncSession,
        portfolio_id: str,
        ledger: PortfolioLedger,
        result: ExecutionBatchResult,
    ) -> None:
        await session.execute(delete(PositionLotRecord).where(PositionLotRecord.portfolio_id == portfolio_id))
        await session.execute(delete(PositionRecord).where(PositionRecord.portfolio_id == portfolio_id))
        for position in ledger.positions:
            if position.quantity == 0:
                continue
            position_id = _stable_id("position", f"{portfolio_id}:{position.symbol}")
            target_pct = position.market_value / result.nav.total_equity
            session.add(
                PositionRecord(
                    id=position_id,
                    portfolio_id=portfolio_id,
                    symbol=position.symbol,
                    quantity=position.quantity,
                    average_cost=position.average_cost,
                    total_cost=position.total_cost,
                    last_price=position.last_price,
                    target_position_pct=target_pct,
                    updated_at=result.snapshot.as_of,
                )
            )
            for index, lot in enumerate(position.lots, start=1):
                if lot.remaining_quantity == 0:
                    continue
                session.add(
                    PositionLotRecord(
                        id=_stable_id(
                            "lot",
                            f"{position_id}:{lot.acquired_on.isoformat()}:{index}",
                        ),
                        portfolio_id=portfolio_id,
                        position_id=position_id,
                        symbol=position.symbol,
                        acquired_on=lot.acquired_on,
                        remaining_quantity=lot.remaining_quantity,
                        unit_cost=lot.unit_cost,
                        total_cost=lot.total_cost,
                    )
                )

    async def _existing_execution(
        self,
        session: AsyncSession,
        command: ExecuteTradeDate,
        orders: tuple[VirtualOrderRecord, ...],
    ) -> ExecutionBatchResult:
        snapshot = await session.scalar(
            select(PortfolioSnapshotRecord)
            .where(
                PortfolioSnapshotRecord.portfolio_id == command.portfolio_id,
                PortfolioSnapshotRecord.snapshot_type == "POST_EXECUTION",
                PortfolioSnapshotRecord.as_of >= datetime.combine(command.trade_date, time.min),
                PortfolioSnapshotRecord.as_of < datetime.combine(
                    command.trade_date + timedelta(days=1), time.min
                ),
            )
        )
        nav = await session.scalar(
            select(NavRecord).where(
                NavRecord.portfolio_id == command.portfolio_id,
                NavRecord.valuation_date == command.trade_date,
            )
        )
        if snapshot is None or nav is None:
            raise PortfolioTransactionError("EXECUTION_RESULT_NOT_FOUND")
        if command.execution_input_hash is not None and (
            snapshot.details_json is None
            or snapshot.details_json.get("execution_input_hash") != command.execution_input_hash
        ):
            raise PortfolioTransactionError("EXECUTION_INPUT_CONFLICT")
        return ExecutionBatchResult(
            orders=tuple(_stored_order(item) for item in orders),
            fills=(),
            snapshot=_portfolio_snapshot(snapshot),
            nav=_nav_state(nav, snapshot),
        )


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _snapshot_record(
    snapshot: PortfolioSnapshot,
    execution_input_hash: str | None = None,
) -> PortfolioSnapshotRecord:
    return PortfolioSnapshotRecord(
        id=_stable_id(
            "snapshot",
            f"{snapshot.portfolio_id}:{snapshot.snapshot_type}:{snapshot.as_of.isoformat()}",
        ),
        portfolio_id=snapshot.portfolio_id,
        snapshot_type=snapshot.snapshot_type,
        as_of=snapshot.as_of,
        cash_balance=snapshot.cash_balance,
        frozen_cash=snapshot.frozen_cash,
        market_value=snapshot.market_value,
        total_equity=snapshot.total_equity,
        realized_pnl=snapshot.realized_pnl,
        unrealized_pnl=snapshot.unrealized_pnl,
        content_hash=snapshot.content_hash,
        details_json=(
            {"execution_input_hash": execution_input_hash}
            if execution_input_hash is not None else None
        ),
    )


def _order_record(order: StoredOrder, portfolio_id: str, now: datetime) -> VirtualOrderRecord:
    return VirtualOrderRecord(
        id=order.order_id,
        idempotency_key=order.idempotency_key,
        portfolio_id=portfolio_id,
        signal_id=order.signal_id,
        symbol=order.symbol,
        side=order.side.value,
        action=order.action.value,
        target_position_pct=order.target_position_pct,
        planned_execution_date=order.planned_execution_date,
        policy_json=order.policy.model_dump(mode="json"),
        status=order.status.value,
        status_code=order.code,
        intended_quantity=order.intended_quantity,
        filled_quantity=order.filled_quantity,
        remaining_quantity=order.remaining_quantity,
        created_at=now,
        updated_at=now,
    )


def _stored_order(record: VirtualOrderRecord) -> StoredOrder:
    return StoredOrder(
        order_id=record.id,
        idempotency_key=record.idempotency_key,
        signal_id=record.signal_id,
        symbol=record.symbol,
        side=OrderSide(record.side),
        action=SignalAction(record.action),
        target_position_pct=record.target_position_pct,
        planned_execution_date=record.planned_execution_date,
        policy=ExecutionPolicy.model_validate(record.policy_json),
        status=VirtualOrderStatus(record.status),
        code=record.status_code,
        intended_quantity=record.intended_quantity,
        filled_quantity=record.filled_quantity,
        remaining_quantity=record.remaining_quantity,
    )


def _fill_record(fill: VirtualFill) -> VirtualFillRecord:
    return VirtualFillRecord(
        id=_stable_id("fill", f"{fill.order_id}:{fill.sequence}"),
        order_id=fill.order_id,
        fill_sequence=fill.sequence,
        symbol=fill.symbol,
        side=fill.side.value,
        quantity=fill.quantity,
        raw_open_price=fill.raw_open_price,
        fill_price=fill.fill_price,
        gross_value=fill.gross_value,
        commission=fill.commission,
        stamp_tax=fill.stamp_tax,
        total_fees=fill.total_fees,
        trade_date=fill.trade_date,
        created_at=datetime.combine(fill.trade_date, time.min, tzinfo=UTC),
    )


def _nav_record(portfolio_id: str, nav: NavState) -> NavRecord:
    return NavRecord(
        id=_stable_id("nav", f"{portfolio_id}:{nav.valuation_date.isoformat()}"),
        portfolio_id=portfolio_id,
        valuation_date=nav.valuation_date,
        total_equity=nav.total_equity,
        unit_nav=nav.unit_nav,
        daily_return=nav.daily_return,
        cumulative_return=nav.cumulative_return,
        max_drawdown=nav.max_drawdown,
    )


def _nav_state(
    record: NavRecord,
    snapshot: PortfolioSnapshotRecord | None = None,
) -> NavState:
    return NavState(
        valuation_date=record.valuation_date,
        cash_balance=snapshot.cash_balance if snapshot is not None else Decimal(0),
        market_value=snapshot.market_value if snapshot is not None else record.total_equity,
        total_equity=record.total_equity,
        realized_pnl=snapshot.realized_pnl if snapshot is not None else Decimal(0),
        unrealized_pnl=snapshot.unrealized_pnl if snapshot is not None else Decimal(0),
        unit_nav=record.unit_nav,
        daily_return=record.daily_return,
        cumulative_return=record.cumulative_return,
        max_drawdown=record.max_drawdown,
    )


def _portfolio_snapshot(record: PortfolioSnapshotRecord) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        portfolio_id=record.portfolio_id,
        snapshot_type=record.snapshot_type,
        as_of=record.as_of,
        cash_balance=record.cash_balance,
        frozen_cash=record.frozen_cash,
        market_value=record.market_value,
        total_equity=record.total_equity,
        realized_pnl=record.realized_pnl,
        unrealized_pnl=record.unrealized_pnl,
        content_hash=record.content_hash,
    )
