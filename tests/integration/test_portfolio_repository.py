import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.domain.decision import DecisionAction
from fkqt_jevinvestor.domain.market import (
    MarketExecutionSnapshot,
    TradingDayStatus,
    TradingStatus,
)
from fkqt_jevinvestor.domain.signals import FixtureSignal, FixtureSignalBatch, SignalAction
from fkqt_jevinvestor.persistence.models import (
    DecisionEvaluationRecord,
    JevEvaluationRecord,
    NavRecord,
    PortfolioSnapshotRecord,
    PositionSizingRunRecord,
    PositionTargetRecord,
    SignalBatchRecord,
    SignalRecord,
    VirtualFillRecord,
    VirtualOrderRecord,
)
from fkqt_jevinvestor.persistence.repositories import (
    PortfolioAlreadyExists,
    PortfolioRepository,
    PortfolioTransactionError,
    PortfolioVersionConflict,
)
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio, ExecuteTradeDate
from fkqt_jevinvestor.services.position_sizing import (
    PositionSizingConfigV1,
    to_validated_signal_batch,
)
from fkqt_jevinvestor.services.signal_validator import validate_signal_batch
from tests.unit.test_position_sizing import (
    _evaluation,  # pyright: ignore[reportPrivateUsage]
    _features,  # pyright: ignore[reportPrivateUsage]
    _run,  # pyright: ignore[reportPrivateUsage]
)

SessionFactory = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[SessionFactory]:
    database = tmp_path / "repository.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    await asyncio.to_thread(command.upgrade, config, "head")
    engine = create_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    yield create_session_factory(engine)
    await engine.dispose()


def signal_batch(portfolio_id: str = "portfolio-1") -> FixtureSignalBatch:
    return FixtureSignalBatch(
        portfolio_id=portfolio_id,
        decision_date=date(2026, 9, 21),
        planned_execution_date=date(2026, 9, 22),
        fixture_version="fixture-v1",
        candidate_symbols=("000001.SZ",),
        signals=(
            FixtureSignal(
                symbol="000001.SZ",
                action=SignalAction.OPEN,
                target_position_pct=Decimal("0.50"),
                confidence=Decimal("0.80"),
                thesis="Phase 1 fixture",
                invalidation="Fixture invalidation",
            ),
        ),
        cash_target_pct=Decimal("0.50"),
    )


def execution_market(
    trade_date: date = date(2026, 9, 22),
    price: Decimal = Decimal(10),
) -> MarketExecutionSnapshot:
    return MarketExecutionSnapshot(
        symbol="000001.SZ",
        trade_date=trade_date,
        trading_day_status=TradingDayStatus.OPEN,
        trading_status=TradingStatus.TRADING,
        open_price=price,
        unadjusted_close=price,
        daily_amount_cny=Decimal(100000000),
        upper_limit_price=Decimal(11),
        lower_limit_price=Decimal(9),
    )


async def accepted_batch(repository: PortfolioRepository) -> object:
    state = await repository.get_state("portfolio-1")
    validated = validate_signal_batch(signal_batch(), state, {"000001.SZ"})
    return await repository.save_signal_batch(validated)


async def scalar_count(session_factory: SessionFactory, model: type[object]) -> int:
    async with session_factory() as session:
        return int((await session.scalar(select(func.count()).select_from(model))) or 0)


async def seed_c_group_evaluation(session_factory: SessionFactory) -> None:
    now = datetime(2026, 9, 18, 15, tzinfo=UTC)
    async with session_factory.begin() as session:
        for evaluation_id, scope, symbol in (
            ("jev-universe", "UNIVERSE", None),
            ("jev-symbol", "SYMBOL", "000001.SZ"),
        ):
            session.add(
                JevEvaluationRecord(
                    id=evaluation_id,
                    formal_key=("1" if scope == "UNIVERSE" else "2") * 64,
                    scope=scope,
                    symbol=symbol,
                    decision_date=now.date(),
                    decision_cutoff=now,
                    state_json={},
                    input_hash="3" * 64,
                    provider_name="typesafe",
                    provider_version="0.7.0",
                    model_id="jev-market",
                    state_schema_version="jev-state-v1",
                    question_set_version="jev-pnl-questions-v1",
                    status="AVAILABLE",
                    latest_attempt_sequence=1,
                    current_owner_token=None,
                    lease_expires_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.add(
            DecisionEvaluationRecord(
                id="decision-000001.SZ",
                formal_key="4" * 64,
                portfolio_id="portfolio-1",
                symbol="000001.SZ",
                membership="CANDIDATE",
                decision_date=now.date(),
                decision_cutoff=now,
                input_json={},
                input_hash="5" * 64,
                universe_jev_evaluation_id="jev-universe",
                symbol_jev_evaluation_id="jev-symbol",
                provider_name="deepseek",
                provider_version="responses-v1",
                model_id="deepseek-flash",
                provider_base_url="https://api.deepseek.com",
                reasoning_effort="high",
                prompt_version="decision-prompt-v1",
                output_schema_version="decision-output-v1",
                status="AVAILABLE",
                action="ENTER",
                thesis="冻结证据一致。",
                invalidation="趋势结构失效。",
                raw_response_text='{"action":"ENTER"}',
                raw_response_hash="6" * 64,
                error_code=None,
                latest_attempt_sequence=1,
                current_owner_token=None,
                lease_expires_at=None,
                created_at=now,
                updated_at=now,
            )
        )


@pytest.mark.asyncio
async def test_create_uses_default_cash_and_writes_initial_snapshot(
    session_factory: SessionFactory,
) -> None:
    repository = PortfolioRepository(session_factory)

    state = await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))

    assert state.cash_balance == Decimal("1000000.0000")
    assert state.version == 1
    assert await scalar_count(session_factory, PortfolioSnapshotRecord) == 1


@pytest.mark.asyncio
async def test_duplicate_portfolio_id_is_rejected(session_factory: SessionFactory) -> None:
    repository = PortfolioRepository(session_factory)
    command_model = CreatePortfolio(portfolio_id="portfolio-1", name="Demo")
    await repository.create(command_model)

    with pytest.raises(PortfolioAlreadyExists):
        await repository.create(command_model)


@pytest.mark.asyncio
async def test_same_fixture_returns_existing_batch_and_orders(session_factory: SessionFactory) -> None:
    repository = PortfolioRepository(session_factory)
    await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
    state = await repository.get_state("portfolio-1")
    validated = validate_signal_batch(signal_batch(), state, {"000001.SZ"})

    first = await repository.save_signal_batch(validated)
    second = await repository.save_signal_batch(validated)

    assert second.batch_id == first.batch_id
    assert second.orders == first.orders
    assert await scalar_count(session_factory, VirtualOrderRecord) == 1
    assert len(await repository.load_pending_orders("portfolio-1", date(2026, 9, 22))) == 1


@pytest.mark.asyncio
async def test_execution_atomically_persists_fill_position_lot_snapshot_and_nav(
    session_factory: SessionFactory,
) -> None:
    repository = PortfolioRepository(session_factory)
    await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
    await accepted_batch(repository)

    result = await repository.execute_trade_date(
        ExecuteTradeDate(
            portfolio_id="portfolio-1",
            trade_date=date(2026, 9, 22),
            expected_version=1,
            market_snapshots={"000001.SZ": execution_market()},
        )
    )
    state = await repository.get_state("portfolio-1")

    assert len(result.fills) == 1
    assert state.version == 2
    assert state.position("000001.SZ") is not None
    assert state.position("000001.SZ").quantity > 0  # type: ignore[union-attr]
    assert await scalar_count(session_factory, VirtualFillRecord) == 1
    assert await repository.list_nav("portfolio-1") == (result.nav,)
    assert await repository.load_pending_orders("portfolio-1", date(2026, 9, 22)) == ()
    same_day = await repository.get_state("portfolio-1", date(2026, 9, 22))
    next_day = await repository.get_state("portfolio-1", date(2026, 9, 23))
    assert same_day.position("000001.SZ").sellable_quantity == 0  # type: ignore[union-attr]
    assert next_day.position("000001.SZ").sellable_quantity > 0  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_no_order_close_restores_nav_history_and_historical_replay(
    session_factory: SessionFactory,
) -> None:
    repository = PortfolioRepository(session_factory)
    await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
    await accepted_batch(repository)
    first = await repository.execute_trade_date(
        ExecuteTradeDate(
            portfolio_id="portfolio-1",
            trade_date=date(2026, 9, 22),
            expected_version=1,
            market_snapshots={"000001.SZ": execution_market()},
        )
    )
    second = await repository.execute_trade_date(
        ExecuteTradeDate(
            portfolio_id="portfolio-1",
            trade_date=date(2026, 9, 23),
            expected_version=2,
            market_snapshots={
                "000001.SZ": execution_market(date(2026, 9, 23), Decimal("9.50"))
            },
        )
    )
    expected_return = (second.nav.total_equity / first.nav.total_equity - 1).quantize(
        Decimal("0.00000001")
    )
    assert second.orders == ()
    assert second.nav.daily_return == expected_return
    assert second.nav.max_drawdown > 0

    replay = await repository.execute_trade_date(
        ExecuteTradeDate(
            portfolio_id="portfolio-1",
            trade_date=date(2026, 9, 22),
            expected_version=3,
            market_snapshots={"000001.SZ": execution_market()},
        )
    )
    assert replay.snapshot.as_of.date() == date(2026, 9, 22)
    assert replay.snapshot.total_equity == replay.nav.total_equity


@pytest.mark.asyncio
async def test_missed_order_expires_on_later_trade_date(
    session_factory: SessionFactory,
) -> None:
    repository = PortfolioRepository(session_factory)
    await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
    await accepted_batch(repository)
    result = await repository.execute_trade_date(
        ExecuteTradeDate(
            portfolio_id="portfolio-1",
            trade_date=date(2026, 9, 23),
            expected_version=1,
            market_snapshots={
                "000001.SZ": execution_market(date(2026, 9, 23), Decimal(10))
            },
        )
    )
    assert result.orders[0].status.value == "EXPIRED"
    assert result.orders[0].code == "SIGNAL_EXPIRED"


@pytest.mark.asyncio
async def test_historical_state_rejects_date_before_latest_nav(
    session_factory: SessionFactory,
) -> None:
    repository = PortfolioRepository(session_factory)
    await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
    await accepted_batch(repository)
    await repository.execute_trade_date(
        ExecuteTradeDate(
            portfolio_id="portfolio-1",
            trade_date=date(2026, 9, 22),
            expected_version=1,
            market_snapshots={"000001.SZ": execution_market()},
        )
    )

    with pytest.raises(
        PortfolioTransactionError,
        match="PORTFOLIO_HISTORICAL_STATE_UNAVAILABLE",
    ):
        await repository.get_state("portfolio-1", date(2026, 9, 18))


@pytest.mark.asyncio
async def test_repeated_execution_does_not_create_another_fill(
    session_factory: SessionFactory,
) -> None:
    repository = PortfolioRepository(session_factory)
    await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
    await accepted_batch(repository)
    first = ExecuteTradeDate(
        portfolio_id="portfolio-1",
        trade_date=date(2026, 9, 22),
        expected_version=1,
        market_snapshots={"000001.SZ": execution_market()},
    )
    await repository.execute_trade_date(first)

    repeated = await repository.execute_trade_date(first.model_copy(update={"expected_version": 2}))

    assert repeated.fills == ()
    assert await scalar_count(session_factory, VirtualFillRecord) == 1


@pytest.mark.asyncio
async def test_repeated_daily_execution_rejects_changed_frozen_input(
    session_factory: SessionFactory,
) -> None:
    repository = PortfolioRepository(session_factory)
    await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
    await accepted_batch(repository)
    first = ExecuteTradeDate(
        portfolio_id="portfolio-1",
        trade_date=date(2026, 9, 22),
        expected_version=1,
        market_snapshots={"000001.SZ": execution_market()},
        execution_input_hash="a" * 64,
    )
    await repository.execute_trade_date(first)
    with pytest.raises(PortfolioTransactionError, match="EXECUTION_INPUT_CONFLICT"):
        await repository.execute_trade_date(first.model_copy(update={
            "expected_version": 2, "execution_input_hash": "b" * 64,
        }))


@pytest.mark.asyncio
async def test_c_group_sizing_signal_and_order_are_saved_atomically(
    session_factory: SessionFactory,
) -> None:
    repository = PortfolioRepository(session_factory)
    await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
    await seed_c_group_evaluation(session_factory)
    state = await repository.get_state("portfolio-1", date(2026, 9, 18))
    evaluation = _evaluation("000001.SZ", DecisionAction.ENTER)
    sizing_run = _run(
        (evaluation,),
        {"000001.SZ": _features("000001.SZ")},
        state,
    )
    validated = to_validated_signal_batch(sizing_run)

    stored = await repository.save_c_group_signal_batch(
        sizing_run,
        PositionSizingConfigV1(),
        validated,
        state,
    )

    assert stored.orders and stored.orders[0].symbol == "000001.SZ"
    assert await scalar_count(session_factory, PositionSizingRunRecord) == 1
    assert await scalar_count(session_factory, PositionTargetRecord) == 1
    assert await scalar_count(session_factory, SignalBatchRecord) == 1
    assert await scalar_count(session_factory, SignalRecord) == 1
    assert await scalar_count(session_factory, VirtualOrderRecord) == 1


@pytest.mark.asyncio
async def test_distinct_runs_reuse_content_addressed_signal_batch(
    session_factory: SessionFactory,
) -> None:
    repository = PortfolioRepository(session_factory)
    await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
    await seed_c_group_evaluation(session_factory)
    state = await repository.get_state("portfolio-1", date(2026, 9, 18))
    evaluation = _evaluation("000001.SZ", DecisionAction.ENTER)
    first_run = _run(
        (evaluation,),
        {"000001.SZ": _features("000001.SZ")},
        state,
    )
    second_run = first_run.model_copy(update={"run_id": "run-2"})

    first = await repository.save_c_group_signal_batch(
        first_run,
        PositionSizingConfigV1(),
        to_validated_signal_batch(first_run),
        state,
    )
    second = await repository.save_c_group_signal_batch(
        second_run,
        PositionSizingConfigV1(),
        to_validated_signal_batch(second_run),
        state,
    )

    assert second.batch_id == first.batch_id
    assert second.orders == first.orders
    assert await scalar_count(session_factory, PositionSizingRunRecord) == 2
    assert await scalar_count(session_factory, PositionTargetRecord) == 2
    assert await scalar_count(session_factory, SignalBatchRecord) == 1
    assert await scalar_count(session_factory, SignalRecord) == 1
    assert await scalar_count(session_factory, VirtualOrderRecord) == 1


@pytest.mark.asyncio
async def test_c_group_target_failure_rolls_back_sizing_signal_and_order(
    session_factory: SessionFactory,
) -> None:
    repository = PortfolioRepository(session_factory)
    await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
    await seed_c_group_evaluation(session_factory)
    state = await repository.get_state("portfolio-1", date(2026, 9, 18))
    evaluation = _evaluation("000001.SZ", DecisionAction.ENTER)
    sizing_run = _run(
        (evaluation,),
        {"000001.SZ": _features("000001.SZ")},
        state,
    )
    invalid_run = sizing_run.model_copy(
        update={"targets": (sizing_run.targets[0], sizing_run.targets[0])}
    )

    with pytest.raises(PortfolioTransactionError, match="C_GROUP_ATOMIC_WRITE_FAILED"):
        await repository.save_c_group_signal_batch(
            invalid_run,
            PositionSizingConfigV1(),
            to_validated_signal_batch(invalid_run),
            state,
        )

    assert await scalar_count(session_factory, PositionSizingRunRecord) == 0
    assert await scalar_count(session_factory, PositionTargetRecord) == 0
    assert await scalar_count(session_factory, SignalBatchRecord) == 0
    assert await scalar_count(session_factory, SignalRecord) == 0
    assert await scalar_count(session_factory, VirtualOrderRecord) == 0


@pytest.mark.asyncio
async def test_stale_version_returns_portfolio_version_conflict(
    session_factory: SessionFactory,
) -> None:
    repository = PortfolioRepository(session_factory)
    await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
    await accepted_batch(repository)
    execute = ExecuteTradeDate(
        portfolio_id="portfolio-1",
        trade_date=date(2026, 9, 22),
        expected_version=1,
        market_snapshots={"000001.SZ": execution_market()},
    )
    await repository.execute_trade_date(execute)

    with pytest.raises(PortfolioVersionConflict, match="PORTFOLIO_VERSION_CONFLICT"):
        await repository.execute_trade_date(execute)

    assert await scalar_count(session_factory, VirtualFillRecord) == 1


@pytest.mark.asyncio
async def test_concurrent_execution_writes_one_fill_and_reports_version_conflict(
    session_factory: SessionFactory,
) -> None:
    repository = PortfolioRepository(session_factory)
    await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
    await accepted_batch(repository)
    execute = ExecuteTradeDate(
        portfolio_id="portfolio-1",
        trade_date=date(2026, 9, 22),
        expected_version=1,
        market_snapshots={"000001.SZ": execution_market()},
    )

    first, second = await asyncio.gather(
        repository.execute_trade_date(execute),
        repository.execute_trade_date(execute),
        return_exceptions=True,
    )

    assert await scalar_count(session_factory, VirtualFillRecord) == 1
    assert sum(isinstance(item, PortfolioVersionConflict) for item in (first, second)) == 1


@pytest.mark.asyncio
async def test_mid_transaction_failure_rolls_back_cash_position_and_order(
    session_factory: SessionFactory,
) -> None:
    repository = PortfolioRepository(session_factory)
    await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
    await accepted_batch(repository)
    before = await repository.get_state("portfolio-1")

    async with session_factory.begin() as session:
        session.add(
            NavRecord(
                id="forced-nav-conflict",
                portfolio_id="portfolio-1",
                valuation_date=date(2026, 9, 22),
                total_equity=Decimal(1000000),
                unit_nav=Decimal(1),
                daily_return=Decimal(0),
                cumulative_return=Decimal(0),
                max_drawdown=Decimal(0),
            )
        )

    with pytest.raises(PortfolioTransactionError):
        await repository.execute_trade_date(
            ExecuteTradeDate(
                portfolio_id="portfolio-1",
                trade_date=date(2026, 9, 22),
                expected_version=1,
                market_snapshots={"000001.SZ": execution_market()},
            )
        )

    after = await repository.get_state("portfolio-1")
    assert after == before
    assert await scalar_count(session_factory, VirtualFillRecord) == 0
    assert len(await repository.load_pending_orders("portfolio-1", date(2026, 9, 22))) == 1
