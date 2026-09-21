import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.domain.decision import (
    DecisionAction,
    DecisionEvaluationCommand,
    DecisionEvaluationStatus,
    DecisionHistoryV1,
    DecisionModelOutputV1,
    DecisionProviderResult,
)
from fkqt_jevinvestor.domain.jev_market import (
    JevEvaluationCommand,
    JevEvaluationStatus,
    JevEvaluationV1,
    JevScope,
)
from fkqt_jevinvestor.domain.market_features import (
    AdjustmentMode,
    DailyBar,
    FeatureValue,
    MarketFeatureSnapshot,
    MarketSnapshot,
    MarketSourceAudit,
    SecurityTradeState,
)
from fkqt_jevinvestor.domain.portfolio import PortfolioState, PositionState
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.persistence.decision_repository import DecisionEvaluationRepository
from fkqt_jevinvestor.persistence.models import (
    JevEvaluationRecord,
    PositionSizingRunRecord,
)
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.providers.base import ProviderContractError
from fkqt_jevinvestor.services.c_group_decision import (
    CGroupDecisionCommandV1,
    CGroupDecisionService,
)
from fkqt_jevinvestor.services.jev_market_service import JevRunEvaluationV1
from fkqt_jevinvestor.services.jev_state_builder import (
    REQUIRED_SYMBOL_FEATURES,
    build_jev_states,
)
from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio
from fkqt_jevinvestor.services.position_sizing import PositionSizingConfigV1
from tests.unit.test_decision_contracts import _jev  # pyright: ignore[reportPrivateUsage]

SessionFactory = async_sessionmaker[AsyncSession]


class FakeDecisionProvider:
    def __init__(self) -> None:
        self.commands: list[DecisionEvaluationCommand] = []

    async def evaluate(
        self,
        command: DecisionEvaluationCommand,
    ) -> DecisionProviderResult:
        self.commands.append(command)
        action = command.decision_input.allowed_actions[0].value
        raw = (
            f'{{"action":"{action}","thesis":"冻结证据一致。",'
            '"invalidation":"趋势结构失效。"}'
        )
        return DecisionProviderResult(
            output=DecisionModelOutputV1.model_validate(
                {
                    "action": action,
                    "thesis": "冻结证据一致。",
                    "invalidation": "趋势结构失效。",
                }
            ),
            raw_response_text=raw,
            raw_response_hash=("9" if action == "ENTER" else "8") * 64,
        )


class IllegalActionProvider(FakeDecisionProvider):
    async def evaluate(
        self,
        command: DecisionEvaluationCommand,
    ) -> DecisionProviderResult:
        self.commands.append(command)
        raise ProviderContractError("DECISION_ACTION_NOT_ALLOWED", "7" * 64)


class BlockingDecisionProvider(FakeDecisionProvider):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def evaluate(
        self,
        command: DecisionEvaluationCommand,
    ) -> DecisionProviderResult:
        self.entered.set()
        await self.release.wait()
        return await super().evaluate(command)


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[SessionFactory]:
    database = tmp_path / "c-group.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    await asyncio.to_thread(alembic_command.upgrade, config, "head")
    engine = create_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    factory = create_session_factory(engine)
    await PortfolioRepository(factory).create(
        CreatePortfolio(portfolio_id="paper-main", name="Paper")
    )
    yield factory
    await engine.dispose()


def _snapshot() -> MarketSnapshot:
    cutoff = datetime(2026, 9, 18, 15, tzinfo=UTC)
    symbols = ("000001.SZ", "000002.SZ", "600000.SH")
    return MarketSnapshot(
        snapshot_id="market-1",
        decision_date=date(2026, 9, 18),
        decision_cutoff=cutoff,
        next_trade_date=date(2026, 9, 21),
        calendar_complete_through=date(2026, 9, 21),
        universe_snapshot_id="universe-v1",
        universe_snapshot_hash="a" * 64,
        daily_bars={
            symbol: (
                DailyBar(
                    symbol=symbol,
                    trade_date=date(2026, 9, 18),
                    open=Decimal(10),
                    high=Decimal("10.2"),
                    low=Decimal("9.8"),
                    close=Decimal("10.1"),
                    previous_close=Decimal(10),
                    volume=Decimal(1000),
                    amount_cny=Decimal(10000),
                    adjustment_mode=AdjustmentMode.QFQ,
                ),
            )
            for symbol in symbols
        },
        security_states={
            symbol: SecurityTradeState(
                symbol=symbol,
                trade_date=date(2026, 9, 18),
                trading_day_status="OPEN",
                trading_status="TRADING",
                is_st_or_delisting_risk=False,
                upper_limit_price=Decimal(11),
                lower_limit_price=Decimal(9),
                is_initial_no_limit_period=False,
                corporate_action_status="NONE",
                market="SZSE" if symbol.endswith(".SZ") else "SSE",
                board="MAIN",
                listing_date=date(2020, 1, 1),
            )
            for symbol in symbols
        },
        source_manifest_ids=("manifest-1",),
        source_audits=(
            MarketSourceAudit(
                upstream_type="FIXTURE",
                upstream_version="v1",
                request_scope={"symbols": list(symbols)},
                data_cutoff=cutoff,
                schema_version="schema-v1",
                fetched_at=cutoff,
                record_count=len(symbols),
                raw_snapshot_ref="fixture",
                content_hash="e" * 64,
            ),
        ),
        content_hash="b" * 64,
    )


def _c_features(symbol: str) -> MarketFeatureSnapshot:
    cutoff = datetime(2026, 9, 18, 15, tzinfo=UTC)
    values = {
        code: FeatureValue(
            feature_code=code,
            feature_version="market-features-v1",
            as_of=cutoff,
            lookback_window=20,
            value=(
                Decimal("0.50")
                if code == "liquidity_percentile"
                else Decimal("0.02")
                if code == "realized_vol_20d"
                else Decimal(index) / Decimal(100)
            ),
            missing_reason=None,
            source_snapshot_hash="b" * 64,
        )
        for index, code in enumerate(REQUIRED_SYMBOL_FEATURES, start=1)
    }
    draft = MarketFeatureSnapshot(
        symbol=symbol,
        decision_date=date(2026, 9, 18),
        values=values,
        content_hash="0" * 64,
    )
    canonical = draft.model_copy(update={"content_hash": ""})
    return draft.model_copy(update={"content_hash": sha256_json(canonical)})


def _available_symbol(symbol: str) -> JevEvaluationV1:
    cutoff = datetime(2026, 9, 18, 15, tzinfo=UTC)
    return _jev(JevScope.SYMBOL, symbol=symbol, finished_at=cutoff).model_copy(
        update={"evaluation_id": f"jev-{symbol}"}
    )


def _failed_symbol(symbol: str) -> JevEvaluationV1:
    available = _available_symbol(symbol)
    return JevEvaluationV1(
        **available.model_dump(
            exclude={"status", "results", "raw_response_hash", "error_code"}
        ),
        status=JevEvaluationStatus.DATA_UNAVAILABLE,
        results=(),
        raw_response_hash=None,
        error_code="REQUIRED_FEATURE_MISSING",
    )


def _jev_run(
    run_id: UUID,
    *,
    universe_status: JevEvaluationStatus = JevEvaluationStatus.AVAILABLE,
) -> JevRunEvaluationV1:
    cutoff = datetime(2026, 9, 18, 15, tzinfo=UTC)
    universe = _jev(JevScope.UNIVERSE, symbol=None, finished_at=cutoff)
    if universe_status is not JevEvaluationStatus.AVAILABLE:
        universe = JevEvaluationV1(
            **universe.model_dump(
                exclude={"status", "results", "raw_response_hash", "error_code"}
            ),
            status=universe_status,
            results=(),
            raw_response_hash=None,
            error_code="UNIVERSE_DATA_UNAVAILABLE",
        )
    features = {
        symbol: _c_features(symbol)
        for symbol in ("000001.SZ", "000002.SZ", "600000.SH")
    }
    universe_state, symbol_states = build_jev_states(
        snapshot=_snapshot(),
        features=features,
        candidate_symbols=("600000.SH", "000001.SZ"),
        held_only_symbols=("000002.SZ",),
        candidate_limit=2,
    )
    universe = universe.model_copy(
        update={
            "input_hash": JevEvaluationCommand(
                scope=JevScope.UNIVERSE,
                state=universe_state,
                provider_name=universe.provider_name,
                provider_version=universe.provider_version,
                model_id=universe.model_id,
            ).input_hash
        }
    )
    symbol_evaluations = {
        "000001.SZ": _failed_symbol("000001.SZ"),
        "000002.SZ": _available_symbol("000002.SZ"),
        "600000.SH": _available_symbol("600000.SH"),
    }
    symbol_evaluations = {
        symbol: evaluation.model_copy(
            update={
                "input_hash": JevEvaluationCommand(
                    scope=JevScope.SYMBOL,
                    state=symbol_states[symbol],
                    provider_name=evaluation.provider_name,
                    provider_version=evaluation.provider_version,
                    model_id=evaluation.model_id,
                ).input_hash
            }
        )
        for symbol, evaluation in symbol_evaluations.items()
    }
    return JevRunEvaluationV1(
        run_id=run_id,
        decision_date=date(2026, 9, 18),
        decision_cutoff=cutoff,
        planned_execution_date=date(2026, 9, 21),
        candidate_universe_hash="a" * 64,
        market_snapshot_hash="b" * 64,
        universe=universe,
        symbols=symbol_evaluations,
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        portfolio_id="paper-main",
        cash_balance=Decimal(950000),
        frozen_cash=Decimal(0),
        realized_pnl=Decimal(0),
        positions=(
            PositionState(
                symbol="000002.SZ",
                quantity=5000,
                sellable_quantity=5000,
                average_cost=Decimal(10),
                total_cost=Decimal(50000),
                last_price=Decimal(10),
                current_position_pct=Decimal("0.05000000"),
                unrealized_pnl=Decimal(0),
                holding_trading_days=5,
            ),
        ),
        version=1,
    )


def _command(run_id: UUID, jev: JevRunEvaluationV1) -> CGroupDecisionCommandV1:
    symbols = ("600000.SH", "000001.SZ")
    evaluated = (*symbols, "000002.SZ")
    return CGroupDecisionCommandV1(
        run_id=run_id,
        snapshot=_snapshot(),
        features={symbol: _c_features(symbol) for symbol in evaluated},
        candidate_symbols=symbols,
        candidate_limit=2,
        jev=jev,
        portfolio=_portfolio(),
        total_equity=Decimal(1000000),
        recent_actions={
            "000002.SZ": (
                DecisionHistoryV1(
                    decision_date=date(2026, 9, 17),
                    action=DecisionAction.KEEP,
                ),
            )
        },
        pending_orders={},
        provider_name="deepseek",
        provider_version="responses-v1",
        model_id="deepseek-flash",
        sizing_config=PositionSizingConfigV1(),
    )


async def _seed_jev(session_factory: SessionFactory, jev: JevRunEvaluationV1) -> None:
    now = datetime(2026, 9, 18, 15, tzinfo=UTC)
    evaluations = (jev.universe, *jev.symbols.values())
    async with session_factory.begin() as session:
        for index, evaluation in enumerate(evaluations):
            session.add(
                JevEvaluationRecord(
                    id=evaluation.evaluation_id,
                    formal_key=f"{index + 1}" * 64,
                    scope=evaluation.scope.value,
                    symbol=evaluation.symbol,
                    decision_date=now.date(),
                    decision_cutoff=now,
                    state_json={},
                    input_hash=evaluation.input_hash,
                    provider_name=evaluation.provider_name,
                    provider_version=evaluation.provider_version,
                    model_id=evaluation.model_id,
                    state_schema_version=evaluation.state_schema_version,
                    question_set_version=evaluation.question_set_version,
                    status=evaluation.status.value,
                    latest_attempt_sequence=1,
                    current_owner_token=None,
                    lease_expires_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )


@pytest.mark.asyncio
async def test_c_group_evaluates_available_symbols_and_keeps_complete_coverage(
    session_factory: SessionFactory,
) -> None:
    run_id = uuid4()
    jev = _jev_run(run_id)
    await _seed_jev(session_factory, jev)
    provider = FakeDecisionProvider()
    service = CGroupDecisionService(
        provider=provider,
        decision_repository=DecisionEvaluationRepository(session_factory),
        portfolio_repository=PortfolioRepository(session_factory),
    )

    result = await service.evaluate_run(_command(run_id, jev))

    assert tuple(item.symbol for item in result.evaluations) == (
        "000001.SZ",
        "000002.SZ",
        "600000.SH",
    )
    assert len(provider.commands) == 2
    held = next(
        item for item in provider.commands if item.decision_input.symbol == "000002.SZ"
    )
    assert held.decision_input.membership.value == "HELD_ONLY"
    assert held.decision_input.allowed_actions == (
        DecisionAction.KEEP,
        DecisionAction.EXIT,
    )
    failed = next(item for item in result.evaluations if item.symbol == "000001.SZ")
    assert failed.status is DecisionEvaluationStatus.DATA_UNAVAILABLE
    assert failed.action is DecisionAction.NO_SIGNAL
    assert len(result.sizing_run.targets) == 3
    assert result.stored_batch_id


@pytest.mark.asyncio
async def test_universe_failure_skips_all_llm_calls_and_same_run_replays_without_calls(
    session_factory: SessionFactory,
) -> None:
    run_id = uuid4()
    jev = _jev_run(run_id, universe_status=JevEvaluationStatus.DATA_UNAVAILABLE)
    await _seed_jev(session_factory, jev)
    provider = FakeDecisionProvider()
    service = CGroupDecisionService(
        provider=provider,
        decision_repository=DecisionEvaluationRepository(session_factory),
        portfolio_repository=PortfolioRepository(session_factory),
    )
    command = _command(run_id, jev)

    first = await service.evaluate_run(command)
    second = await service.evaluate_run(command)

    assert provider.commands == []
    assert all(
        item.status is DecisionEvaluationStatus.DATA_UNAVAILABLE
        for item in first.evaluations
    )
    assert second.stored_batch_id == first.stored_batch_id


@pytest.mark.asyncio
async def test_c_group_rejects_missing_jev_symbol_before_any_provider_call(
    session_factory: SessionFactory,
) -> None:
    run_id = uuid4()
    jev = _jev_run(run_id)
    jev = jev.model_copy(update={"symbols": {"600000.SH": jev.symbols["600000.SH"]}})
    provider = FakeDecisionProvider()
    service = CGroupDecisionService(
        provider=provider,
        decision_repository=DecisionEvaluationRepository(session_factory),
        portfolio_repository=PortfolioRepository(session_factory),
    )

    with pytest.raises(ValueError, match="C_GROUP_JEV_COVERAGE_INCOMPLETE"):
        await service.evaluate_run(_command(run_id, jev))

    assert provider.commands == []


@pytest.mark.asyncio
async def test_c_group_rejects_tampered_feature_and_jev_bindings_before_calls(
    session_factory: SessionFactory,
) -> None:
    run_id = uuid4()
    jev = _jev_run(run_id)
    provider = FakeDecisionProvider()
    service = CGroupDecisionService(
        provider=provider,
        decision_repository=DecisionEvaluationRepository(session_factory),
        portfolio_repository=PortfolioRepository(session_factory),
    )
    command = _command(run_id, jev)
    symbol = "600000.SH"
    feature = command.features[symbol]
    tampered_feature = feature.model_copy(update={"content_hash": "f" * 64})

    with pytest.raises(ValueError, match="C_GROUP_FEATURE_CONTENT_HASH_MISMATCH"):
        await service.evaluate_run(
            command.model_copy(
                update={"features": dict(command.features) | {symbol: tampered_feature}}
            )
        )

    values = dict(feature.values)
    first_code = next(iter(values))
    values[first_code] = values[first_code].model_copy(
        update={"source_snapshot_hash": "f" * 64}
    )
    wrong_source = feature.model_copy(update={"values": values, "content_hash": "0" * 64})
    wrong_source = wrong_source.model_copy(
        update={
            "content_hash": sha256_json(
                wrong_source.model_copy(update={"content_hash": ""})
            )
        }
    )
    with pytest.raises(ValueError, match="C_GROUP_FEATURE_SOURCE_MISMATCH"):
        await service.evaluate_run(
            command.model_copy(
                update={"features": dict(command.features) | {symbol: wrong_source}}
            )
        )

    tampered_jev = jev.model_copy(
        update={
            "universe": jev.universe.model_copy(update={"input_hash": "f" * 64})
        }
    )
    with pytest.raises(ValueError, match="C_GROUP_JEV_INPUT_MISMATCH"):
        await service.evaluate_run(command.model_copy(update={"jev": tampered_jev}))

    assert provider.commands == []


@pytest.mark.asyncio
async def test_in_progress_decision_never_persists_sizing_or_signal(
    session_factory: SessionFactory,
) -> None:
    run_id = uuid4()
    jev = _jev_run(run_id)
    await _seed_jev(session_factory, jev)
    blocker = BlockingDecisionProvider()
    first_service = CGroupDecisionService(
        provider=blocker,
        decision_repository=DecisionEvaluationRepository(session_factory),
        portfolio_repository=PortfolioRepository(session_factory),
    )
    second_service = CGroupDecisionService(
        provider=FakeDecisionProvider(),
        decision_repository=DecisionEvaluationRepository(session_factory),
        portfolio_repository=PortfolioRepository(session_factory),
    )
    command = _command(run_id, jev)
    first_task = asyncio.create_task(first_service.evaluate_run(command))
    await blocker.entered.wait()

    with pytest.raises(RuntimeError, match="C_GROUP_DECISIONS_IN_PROGRESS"):
        await second_service.evaluate_run(command)
    async with session_factory() as session:
        count = await session.scalar(select(func.count(PositionSizingRunRecord.id)))
    assert count == 0

    blocker.release.set()
    await first_task


@pytest.mark.asyncio
async def test_candidate_order_does_not_change_targets_or_hashes(
    session_factory: SessionFactory,
) -> None:
    run_id = uuid4()
    jev = _jev_run(run_id)
    await _seed_jev(session_factory, jev)
    provider = FakeDecisionProvider()
    service = CGroupDecisionService(
        provider=provider,
        decision_repository=DecisionEvaluationRepository(session_factory),
        portfolio_repository=PortfolioRepository(session_factory),
    )
    command = _command(run_id, jev)

    first = await service.evaluate_run(command)
    second = await service.evaluate_run(
        command.model_copy(update={"candidate_symbols": tuple(reversed(command.candidate_symbols))})
    )

    assert first.sizing_run.input_hash == second.sizing_run.input_hash
    assert first.sizing_run.target_batch_hash == second.sizing_run.target_batch_hash
    assert first.sizing_run.targets == second.sizing_run.targets


@pytest.mark.asyncio
async def test_provider_contract_failure_is_no_signal_and_preserves_response_hash(
    session_factory: SessionFactory,
) -> None:
    run_id = uuid4()
    jev = _jev_run(run_id)
    await _seed_jev(session_factory, jev)
    provider = IllegalActionProvider()
    service = CGroupDecisionService(
        provider=provider,
        decision_repository=DecisionEvaluationRepository(session_factory),
        portfolio_repository=PortfolioRepository(session_factory),
    )

    result = await service.evaluate_run(_command(run_id, jev))

    model_results = tuple(
        item
        for item in result.evaluations
        if item.status is DecisionEvaluationStatus.CONTRACT_INVALID
    )
    assert len(model_results) == 2
    assert all(item.action is DecisionAction.NO_SIGNAL for item in model_results)
    assert all(item.raw_response_hash == "7" * 64 for item in model_results)


@pytest.mark.asyncio
async def test_preexisting_gross_limit_only_blocks_new_entries(
    session_factory: SessionFactory,
) -> None:
    run_id = uuid4()
    jev = _jev_run(run_id)
    await _seed_jev(session_factory, jev)
    provider = FakeDecisionProvider()
    service = CGroupDecisionService(
        provider=provider,
        decision_repository=DecisionEvaluationRepository(session_factory),
        portfolio_repository=PortfolioRepository(session_factory),
    )
    command = _command(run_id, jev)
    portfolio = command.portfolio.model_copy(
        update={
            "positions": (
                command.portfolio.positions[0].model_copy(
                    update={"current_position_pct": Decimal("0.85000000")}
                ),
            )
        }
    )

    result = await service.evaluate_run(command.model_copy(update={"portfolio": portfolio}))

    targets = {item.symbol: item for item in result.sizing_run.targets}
    assert targets["600000.SH"].block_code == "PREEXISTING_GROSS_LIMIT_EXCEEDED"
    assert targets["600000.SH"].target_position_pct == 0
    assert targets["000002.SZ"].target_position_pct <= Decimal("0.85000000")
